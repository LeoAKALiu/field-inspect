"""统一错误格式：{"error": {"code", "message", "details?"}}。"""

from __future__ import annotations

from fastapi import HTTPException

DEFAULT_CODES = {
    400: "bad_request",
    404: "not_found",
    409: "conflict",
    501: "not_implemented",
}


def raise_error(status_code: int, code: str, message: str, details: dict | None = None) -> None:
    detail: dict = {"code": code, "message": message}
    if details is not None:
        detail["details"] = details
    raise HTTPException(status_code=status_code, detail=detail)


def not_found(resource: str, resource_id: str) -> None:
    raise_error(404, "not_found", f"{resource} {resource_id} 不存在")
