"""确定性模拟器：进程内 asyncio 任务，懒启动（首个订阅者连接时启动）。

固定 TWIN_SEED 下，车辆轨迹由 tick 序号驱动、检测事件由定种子伪随机驱动，
同一任务两次运行的事件序列一致。
"""

from __future__ import annotations

import asyncio
import json
import math
import random

from .db import Database
from .provenance import make_provenance
from .utils import envelope, utcnow_iso

EVENT_TYPES = ["crack", "water_leakage", "spalling", "corrosion", "equipment_fault", "obstacle"]
SEVERITIES = ["low", "medium", "high", "critical"]
SPEED_MPS = 1.2
ROUTE_MARGIN_M = 2.0
EVENT_PERIOD_TICKS = 25  # 每 25 个 tick 在偏移 10 处产生一次事件


class SimulationEngine:
    def __init__(self, db: Database, tick_ms: int = 1000, seed: int = 42):
        self.db = db
        self.tick_ms = tick_ms
        self.seed = seed
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._states: dict[str, dict] = {}
        self._runner: asyncio.Task | None = None

    # ---- 订阅管理 ----

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs.setdefault(task_id, set()).add(queue)
        self._states.setdefault(
            task_id,
            {
                "tick": 0,
                "rng": random.Random(f"{self.seed}:{task_id}"),
                "route": self._load_route(task_id),
            },
        )
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run())
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        queues = self._subs.get(task_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                del self._subs[task_id]

    async def stop(self) -> None:
        if self._runner is not None and not self._runner.done():
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass

    # ---- 内部 ----

    def _load_route(self, task_id: str) -> dict:
        """巡检路线：取 scene_metadata 的 route 折线（scene_local_yup，与 Grok viewer 帧同构）。"""
        row = self.db.query_one(
            "SELECT t.scene_id, m.data AS meta FROM tasks t"
            " LEFT JOIN scene_metadata m ON m.scene_id = t.scene_id WHERE t.id = ?",
            (task_id,),
        )
        scene_id = row["scene_id"] if row else None
        points: list[tuple[float, float, float]] = []
        if row and row["meta"]:
            points = [
                (p["x"], p["y"], p["z"])
                for p in json.loads(row["meta"]).get("route", [])
            ]
        if len(points) < 2:  # 无元数据时的兜底直线段
            points = [(ROUTE_MARGIN_M, 0.0, 0.0), (600.0 - ROUTE_MARGIN_M, 0.0, 0.0)]
        cum = [0.0]
        for a, b in zip(points, points[1:], strict=False):
            cum.append(cum[-1] + math.dist(a, b))
        return {"points": points, "cum": cum, "total": max(cum[-1], 1.0), "scene_id": scene_id}

    def _sample_route(self, st: dict) -> tuple[tuple[float, float, float], float]:
        """按 tick 匀速采样路线，返回 (position, heading_deg)。heading 绕 +Y，0°=+Z 方向。"""
        route = st["route"]
        pts, cum = route["points"], route["cum"]
        dist = (st["tick"] * SPEED_MPS * self.tick_ms / 1000.0) % route["total"]
        for i in range(len(pts) - 1):
            if cum[i] <= dist <= cum[i + 1]:
                seg = cum[i + 1] - cum[i]
                t = 0.0 if seg == 0 else (dist - cum[i]) / seg
                a, b = pts[i], pts[i + 1]
                pos = tuple(round(a[k] + (b[k] - a[k]) * t, 3) for k in range(3))
                heading = math.degrees(math.atan2(b[0] - a[0], b[2] - a[2])) % 360.0
                return pos, round(heading, 2)
        return pts[-1], 0.0

    def _vehicle_state(self, task_id: str, st: dict) -> dict:
        pos, heading = self._sample_route(st)
        return {
            "task_id": task_id,
            "timestamp": utcnow_iso(),
            "position": {"x": pos[0], "y": pos[1], "z": pos[2]},
            "heading_deg": heading,
            "speed_mps": SPEED_MPS,
            "battery_pct": round(100.0 - (st["tick"] * 0.02) % 80.0, 2),
            "source_type": "simulation",
            "provenance": make_provenance("simulation", "simulated"),
        }

    def _maybe_event(self, task_id: str, st: dict) -> dict | None:
        tick = st["tick"]
        if tick % EVENT_PERIOD_TICKS != 10:
            return None
        rng: random.Random = st["rng"]
        pos, _ = self._sample_route(st)
        event = {
            "id": f"evt-sim-{task_id}-{tick:06d}",
            "scene_id": st["route"]["scene_id"] or "",
            "task_id": task_id,
            "type": rng.choice(EVENT_TYPES),
            "severity": rng.choice(SEVERITIES),
            "status": "open",
            "position": {"x": pos[0], "y": pos[1], "z": pos[2]},
            "description": "模拟器例行扫描发现的疑似异常",
            "confidence": round(rng.uniform(0.5, 0.99), 3),
            "image_ref": None,
            "detected_at": utcnow_iso(),
            "handled_by": None,
            "handled_at": None,
            "handle_comment": None,
            "source_type": "simulation",
            "provenance": make_provenance("simulation", "simulated"),
        }
        self.db.execute(
            "INSERT OR REPLACE INTO events (id, scene_id, task_id, type, severity, status,"
            " x, y, z, description, confidence, image_ref, detected_at,"
            " handled_by, handled_at, handle_comment)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event["id"], event["scene_id"], event["task_id"], event["type"],
                event["severity"], event["status"], event["position"]["x"],
                event["position"]["y"], event["position"]["z"], event["description"],
                event["confidence"], event["image_ref"], event["detected_at"],
                None, None, None,
            ),
        )
        self.db.execute(
            "UPDATE tasks SET event_count = event_count + 1 WHERE id = ?", (task_id,)
        )
        return event

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.tick_ms / 1000.0)
                for task_id, queues in list(self._subs.items()):
                    if not queues:
                        continue
                    st = self._states[task_id]
                    st["tick"] += 1
                    messages = [envelope("vehicle.state", self._vehicle_state(task_id, st))]
                    event = self._maybe_event(task_id, st)
                    if event is not None:
                        messages.append(envelope("event.created", event))
                    for queue in list(queues):
                        for msg in messages:
                            if queue.full():
                                try:
                                    queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass
                            queue.put_nowait(msg)
        except asyncio.CancelledError:
            pass
