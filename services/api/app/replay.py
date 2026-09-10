"""回放引擎：按轨迹点时间间隔 × (1/speed) 推送，支持 pause/resume/seek/speed。

回放只读：不修改任务与事件的历史状态。
"""

from __future__ import annotations

import asyncio

from starlette.websockets import WebSocket, WebSocketDisconnect

from .utils import envelope, parse_dt

_POLL_SEC = 0.02  # 等待切片，保证控制消息响应及时


async def run_replay(websocket: WebSocket, task_id: str, points: list[dict]) -> None:
    total = len(points)
    state = {
        "index": 0,
        "speed": 1.0,
        "paused": False,
        "finished_sent": False,
        "stop": False,
    }
    await websocket.send_json(
        envelope("replay.started", {"task_id": task_id, "total_points": total, "speed": 1.0})
    )

    async def receiver() -> None:
        try:
            while True:
                msg = await websocket.receive_json()
                if not isinstance(msg, dict) or msg.get("type") != "replay.control":
                    continue
                payload = msg.get("payload") or {}
                action = payload.get("action")
                if action == "pause":
                    state["paused"] = True
                elif action == "resume":
                    state["paused"] = False
                elif action == "seek" and isinstance(payload.get("seq"), int):
                    state["index"] = max(0, min(payload["seq"], total))
                    state["finished_sent"] = False
                elif action == "speed" and payload.get("speed"):
                    state["speed"] = max(0.01, min(float(payload["speed"]), 100.0))
        except (WebSocketDisconnect, RuntimeError):
            state["stop"] = True

    recv_task = asyncio.create_task(receiver())
    try:
        while not state["stop"] and not recv_task.done():
            if state["paused"]:
                await asyncio.sleep(_POLL_SEC)
                continue
            idx = state["index"]
            if idx >= total:
                if not state["finished_sent"]:
                    await websocket.send_json(
                        envelope(
                            "replay.finished",
                            {"task_id": task_id, "total_points": total},
                        )
                    )
                    state["finished_sent"] = True
                await asyncio.sleep(_POLL_SEC)
                continue
            point = points[idx]
            await websocket.send_json(
                envelope(
                    "replay.point",
                    {"point": point, "progress": round((idx + 1) / total, 6)},
                )
            )
            state["index"] = idx + 1
            if idx + 1 < total:
                delta = (
                    parse_dt(points[idx + 1]["timestamp"])
                    - parse_dt(point["timestamp"])
                ).total_seconds()
                remaining = max(delta, 0.0)
                while (
                    remaining > 0.001
                    and not state["stop"]
                    and not state["paused"]
                    and not recv_task.done()
                ):
                    step = min(_POLL_SEC, remaining / state["speed"])
                    await asyncio.sleep(step)
                    remaining -= step * state["speed"]
    finally:
        recv_task.cancel()
