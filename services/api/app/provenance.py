"""对象级数据出处（provenance）构造。

契约约定：当前所有对象的 status 只能是 simulated 或 pending_confirmation，
不存在 confirmed（甲方协议未确认，不虚构）。
"""

from __future__ import annotations

VALID_STATUSES = ("simulated", "pending_confirmation")


VALID_SOURCES = ("simulation", "replay", "live_pending")


def make_provenance(source: str, status: str) -> dict:
    if source not in VALID_SOURCES:
        raise ValueError(f"非法 provenance source: {source}")
    if status not in VALID_STATUSES:
        raise ValueError(f"非法 provenance status: {status}")
    return {"source": source, "status": status}
