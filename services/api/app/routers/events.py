"""异常检测事件：查询、处置状态机、导出。"""

import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from ..db import Database, event_to_dict
from ..errors import not_found, raise_error
from ..utils import utcnow_iso
from . import get_db

router = APIRouter()

EventStatus = Literal["open", "acknowledged", "resolved", "false_positive"]
EventSeverity = Literal["low", "medium", "high", "critical"]
EventType = Literal[
    "crack", "water_leakage", "spalling", "corrosion", "equipment_fault", "obstacle"
]

# 状态机：open → acknowledged → resolved | false_positive
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open": {"acknowledged"},
    "acknowledged": {"resolved", "false_positive"},
    "resolved": set(),
    "false_positive": set(),
}

_EVENT_SELECT = (
    "SELECT e.*, t.mode AS task_mode FROM events e"
    " LEFT JOIN tasks t ON t.id = e.task_id"
)

CSV_COLUMNS = [
    "id", "scene_id", "task_id", "type", "severity", "status",
    "x", "y", "z", "description", "confidence", "image_ref",
    "detected_at", "handled_by", "handled_at", "handle_comment",
]


class EventStatusUpdate(BaseModel):
    status: Literal["acknowledged", "resolved", "false_positive"]
    comment: str | None = None
    handled_by: str | None = None


def _query_events(
    db: Database,
    scene_id: str | None,
    task_id: str | None,
    status: str | None,
    severity: str | None,
    event_type: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list:
    where, params = [], []
    if scene_id is not None:
        where.append("e.scene_id = ?")
        params.append(scene_id)
    if task_id is not None:
        where.append("e.task_id = ?")
        params.append(task_id)
    if status is not None:
        where.append("e.status = ?")
        params.append(status)
    if severity is not None:
        where.append("e.severity = ?")
        params.append(severity)
    if event_type is not None:
        where.append("e.type = ?")
        params.append(event_type)
    sql = _EVENT_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY e.detected_at DESC, e.id ASC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    return db.query_all(sql, tuple(params))


def _render_export(events: list[dict], format: str):
    if format == "json":
        return events
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for e in events:
        writer.writerow(
            [
                e["id"], e["scene_id"], e["task_id"], e["type"], e["severity"], e["status"],
                e["position"]["x"], e["position"]["y"], e["position"]["z"],
                e["description"] or "", e["confidence"] if e["confidence"] is not None else "",
                e["image_ref"] or "", e["detected_at"],
                e["handled_by"] or "", e["handled_at"] or "", e["handle_comment"] or "",
            ]
        )
    return Response(content=buf.getvalue(), media_type="text/csv")


class EventExportRequest(BaseModel):
    format: Literal["csv", "json"]
    scene_id: str | None = None
    task_id: str | None = None
    status: EventStatus | None = None
    severity: EventSeverity | None = None
    type: EventType | None = None


# 注意：/events/export 必须先于 /events/{event_id} 注册
@router.post("/events/export")
def export_events_post(body: EventExportRequest, db: Database = Depends(get_db)):
    rows = _query_events(
        db, body.scene_id, body.task_id, body.status, body.severity, body.type
    )
    return _render_export([event_to_dict(r) for r in rows], body.format)


@router.get("/events/export")
def export_events(
    format: Literal["csv", "json"],
    scene_id: str | None = None,
    task_id: str | None = None,
    status: EventStatus | None = None,
    severity: EventSeverity | None = None,
    db: Database = Depends(get_db),
):
    rows = _query_events(db, scene_id, task_id, status, severity)
    return _render_export([event_to_dict(r) for r in rows], format)


@router.get("/events")
def list_events(
    scene_id: str | None = None,
    task_id: str | None = None,
    status: EventStatus | None = None,
    severity: EventSeverity | None = None,
    type: EventType | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_db),
) -> list[dict]:
    rows = _query_events(db, scene_id, task_id, status, severity, type, limit, offset)
    return [event_to_dict(r) for r in rows]


@router.get("/events/{event_id}")
def get_event(event_id: str, db: Database = Depends(get_db)) -> dict:
    row = db.query_one(_EVENT_SELECT + " WHERE e.id = ?", (event_id,))
    if row is None:
        not_found("事件", event_id)
    return event_to_dict(row)


@router.patch("/events/{event_id}")
def update_event_status(
    event_id: str, body: EventStatusUpdate, db: Database = Depends(get_db)
) -> dict:
    row = db.query_one(_EVENT_SELECT + " WHERE e.id = ?", (event_id,))
    if row is None:
        not_found("事件", event_id)
    current = row["status"]
    if body.status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise_error(
            409,
            "invalid_state_transition",
            f"事件 {event_id} 当前状态为 {current}，不能迁移到 {body.status}",
            {"current_status": current, "requested_status": body.status},
        )
    db.execute(
        "UPDATE events SET status = ?, handled_at = ?, handled_by = ?, handle_comment = ?"
        " WHERE id = ?",
        (body.status, utcnow_iso(), body.handled_by, body.comment, event_id),
    )
    return event_to_dict(db.query_one(_EVENT_SELECT + " WHERE e.id = ?", (event_id,)))
