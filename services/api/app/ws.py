"""WebSocket 网关：车辆位置推送 / 任务回放通道。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .db import point_to_dict
from .access import allowed
from .replay import run_replay
from .utils import envelope, utcnow_iso

router = APIRouter()


def _ack(channel: str, task_id: str | None) -> dict:
    return envelope(
        "connection.ack",
        {"server_time": utcnow_iso(), "channel": channel, "task_id": task_id},
    )


async def _reject(websocket: WebSocket, code: str, message: str) -> None:
    await websocket.send_json(envelope("error", {"code": code, "message": message}))
    await websocket.close(code=1008)


@router.websocket("/ws/vehicle")
async def vehicle_channel(websocket: WebSocket, task_id: str = "") -> None:
    if not allowed(websocket.scope, websocket.headers):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    db = websocket.app.state.db
    engine = websocket.app.state.engine
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,)) if task_id else None
    if task is None or task["mode"] != "simulation":
        await _reject(
            websocket,
            "not_found",
            f"任务 {task_id or '<missing>'} 不存在或非 simulation 模式",
        )
        return
    await websocket.send_json(_ack("vehicle", task_id))

    queue = engine.subscribe(task_id)

    async def forward() -> None:
        try:
            while True:
                msg = await queue.get()
                await websocket.send_json(msg)
        except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            pass

    fwd_task = asyncio.create_task(forward())
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        fwd_task.cancel()
        engine.unsubscribe(task_id, queue)


@router.websocket("/ws/replay/{task_id}")
async def replay_channel(websocket: WebSocket, task_id: str) -> None:
    if not allowed(websocket.scope, websocket.headers):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    db = websocket.app.state.db
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if task is None:
        await _reject(websocket, "not_found", f"任务 {task_id} 不存在")
        return
    await websocket.send_json(_ack("replay", task_id))
    rows = db.query_all(
        "SELECT * FROM trajectory_points WHERE task_id = ? ORDER BY seq ASC", (task_id,)
    )
    source = "replay" if task["mode"] == "replay" else "simulation"
    points = [point_to_dict(r, source) for r in rows]
    await run_replay(websocket, task_id, points)
