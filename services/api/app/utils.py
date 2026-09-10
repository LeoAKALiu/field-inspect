"""时间与报文工具。时间一律 ISO 8601 UTC（YYYY-MM-DDTHH:MM:SSZ）。"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow_iso() -> str:
    return iso(utcnow())


def parse_dt(s: str) -> datetime:
    """解析 ISO 8601 时间（支持 Z 后缀），naive 视为 UTC。"""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def envelope(msg_type: str, payload: dict) -> dict:
    """WS 服务端统一信封 {type, ts, payload}。"""
    return {"type": msg_type, "ts": utcnow_iso(), "payload": payload}
