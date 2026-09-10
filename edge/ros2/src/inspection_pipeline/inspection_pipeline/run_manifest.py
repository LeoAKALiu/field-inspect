"""Pure helpers for inspection run directories and manifests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROUTE_EXECUTION_STATES = frozenset(
    {
        "arming",
        "executing",
        "paused",
        "at_station",
        "completed",
        "completed_with_exceptions",
        "canceled",
    }
)


def now_utc() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def config_hash_from_path(config_path: Path) -> str:
    """Return the SHA256 hex digest for ``config_path``."""
    if not config_path.is_file():
        return "0" * 64
    digest = hashlib.sha256()
    digest.update(config_path.read_bytes())
    return digest.hexdigest()


def git_commit() -> str:
    """Return the current git commit hash when available."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def build_manifest(
    *,
    run_id: str,
    status: str,
    started_at: datetime,
    ended_at: Optional[datetime],
    config_path: Path,
    run_dir: Path,
    storage_id: str,
    exit_reason: Optional[str],
    run_mode: str = "commissioning",
    camera_calibration: Optional[dict[str, Any]] = None,
    fixed_route: Optional[dict[str, Any]] = None,
    route_execution: Optional[dict[str, Any]] = None,
    recording: Optional[dict[str, Any]] = None,
    bag_summary: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """Build a manifest payload matching ``manifest.schema.json``."""
    bag: dict[str, Any] = {
        "storage_id": storage_id,
        "path": str(run_dir / "bag" / run_id),
    }
    if bag_summary is not None:
        bag.update(bag_summary)
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": status,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "ended_at": None if ended_at is None else ended_at.isoformat().replace("+00:00", "Z"),
        "git_commit": git_commit(),
        "config_sha256": config_hash_from_path(config_path),
        "run_mode": run_mode,
        "model_version": os.environ.get("SCOUT_MODEL_VERSION"),
        "camera_calibration": camera_calibration,
        "fixed_route": fixed_route,
        "route_execution": route_execution,
        "recording": recording,
        "bag": bag,
        "artifacts": [],
        "exit_reason": exit_reason,
    }


def stop_outcome_from_route(
    route_execution: Optional[dict[str, Any]],
    *,
    route_required: bool = False,
) -> tuple[str, str]:
    """Map a supervised-route terminal state onto the inspection run outcome."""
    if route_execution is None:
        if route_required:
            return "incomplete", "route_not_started"
        return "completed", "stopped_by_service"
    state = route_execution.get("state")
    attempts = route_execution.get("attempts", 1)
    if state == "completed" and attempts == 1:
        return "completed", "route_completed"
    if state in {"completed", "completed_with_exceptions"}:
        return "completed_with_exceptions", f"route_{state}"
    reason = str(route_execution.get("reason") or "unknown")
    return "incomplete", f"route_{state or 'unknown'}:{reason}"


def route_status_matches_snapshot(
    fixed_route: Optional[dict[str, Any]],
    *,
    route_id: str,
    route_sha256: str,
) -> bool:
    """Return whether a route diagnostic belongs to the frozen run input."""
    return bool(
        fixed_route
        and route_id == fixed_route.get("route_id")
        and route_sha256 == fixed_route.get("sha256")
    )


def update_route_execution(
    current: Optional[dict[str, Any]],
    attempts: int,
    *,
    run_started_ns: int,
    execution_id: str,
    execution_started_ns: int,
    state: str,
    reason: str,
) -> tuple[Optional[dict[str, Any]], int]:
    """Accept only ordered route executions that started within the current run."""
    if (
        not execution_id
        or execution_started_ns < run_started_ns
        or state not in ROUTE_EXECUTION_STATES
        or not reason
    ):
        return current, attempts
    if current is not None:
        current_started_ns = current["execution_started_ns"]
        if execution_started_ns < current_started_ns:
            return current, attempts
        if execution_id != current["execution_id"]:
            if execution_started_ns == current_started_ns:
                return current, attempts
            attempts += 1
    else:
        attempts = 1
    return (
        {
            "execution_id": execution_id,
            "execution_started_ns": execution_started_ns,
            "state": state,
            "reason": reason,
            "attempts": attempts,
        },
        attempts,
    )


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Atomically and durably write ``manifest`` as formatted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
