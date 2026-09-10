"""巡检任务查询与轨迹。"""

from typing import Literal

from fastapi import APIRouter, Depends, Query

from ..acceptance import acceptance_summary_for
from ..db import Database, point_to_dict, task_to_dict
from ..display_trajectory import build_display_trajectory
from ..errors import not_found
from . import get_db

router = APIRouter()

TRAJECTORY_PAGE_DEFAULT = 10_000
TRAJECTORY_MAX = 1_000_000


def _public_task(row, db: Database) -> dict:
    payload = task_to_dict(row)
    if payload["run_kind"] == "recorded":
        payload["acceptance_summary"] = acceptance_summary_for(db, payload["id"])
    else:
        payload["acceptance_summary"] = None
    return payload


TaskStatus = Literal["pending", "running", "completed", "completed_with_exceptions", "aborted"]
TaskMode = Literal["simulation", "replay", "live_pending"]
RunKind = Literal["demonstration", "recorded"]


@router.get("/tasks")
def list_tasks(
    scene_id: str | None = None,
    status: TaskStatus | None = None,
    mode: TaskMode | None = None,
    run_kind: RunKind | None = None,
    db: Database = Depends(get_db),
) -> list[dict]:
    where, params = [], []
    if scene_id is not None:
        where.append("scene_id = ?")
        params.append(scene_id)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if mode is not None:
        where.append("mode = ?")
        params.append(mode)
    if run_kind is not None:
        where.append("run_kind = ?")
        params.append(run_kind)
    sql = "SELECT * FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(actual_end, planned_start, '') DESC, id ASC"
    return [_public_task(r, db) for r in db.query_all(sql, tuple(params))]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Database = Depends(get_db)) -> dict:
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if row is None:
        not_found("任务", task_id)
    return _public_task(row, db)


@router.get("/tasks/{task_id}/trajectory")
def get_task_trajectory(
    task_id: str,
    from_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=TRAJECTORY_PAGE_DEFAULT, ge=1, le=TRAJECTORY_MAX),
    db: Database = Depends(get_db),
) -> list[dict]:
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if task is None:
        not_found("任务", task_id)
    rows = db.query_all(
        "SELECT * FROM trajectory_points WHERE task_id = ? AND seq >= ? ORDER BY seq ASC LIMIT ?",
        (task_id, from_seq, limit),
    )
    run_kind = task["run_kind"] if "run_kind" in task.keys() else "demonstration"
    source = "replay" if task["mode"] == "replay" or run_kind == "recorded" else "simulation"
    provenance_status = "pending_confirmation" if run_kind == "recorded" else "simulated"
    return [point_to_dict(r, source, provenance_status) for r in rows]


def _task_points(task, task_id: str, db: Database) -> list[dict]:
    rows = db.query_all(
        "SELECT * FROM trajectory_points WHERE task_id = ? ORDER BY seq ASC",
        (task_id,),
    )
    run_kind = task["run_kind"] if "run_kind" in task.keys() else "demonstration"
    source = "replay" if task["mode"] == "replay" or run_kind == "recorded" else "simulation"
    provenance_status = "pending_confirmation" if run_kind == "recorded" else "simulated"
    return [point_to_dict(row, source, provenance_status) for row in rows]


@router.get("/tasks/{task_id}/display-trajectory")
def get_task_display_trajectory(task_id: str, db: Database = Depends(get_db)) -> dict:
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if task is None:
        not_found("任务", task_id)
    points = _task_points(task, task_id, db)
    alignment_id = task["alignment_id"] if "alignment_id" in task.keys() else None
    landmarks: list[dict] = []
    if alignment_id:
        for row in db.query_all(
            "SELECT landmark_id, pos_x, pos_y, pos_z FROM registered_landmarks "
            "WHERE alignment_id = ?",
            (alignment_id,),
        ):
            landmarks.append(
                {
                    "landmark_id": row["landmark_id"],
                    "x": row["pos_x"],
                    "y": row["pos_y"],
                    "z": row["pos_z"],
                }
            )
    return build_display_trajectory(points, landmarks)
