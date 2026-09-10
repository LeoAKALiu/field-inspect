"""Derive vehicle station evidence from recorded cumulative route diagnostics."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from typing import Iterable

from inspection_pipeline.run_manifest import ROUTE_EXECUTION_STATES

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class StationEvidenceError(ValueError):
    """Recorded route evidence is missing, contradictory or unsafe to publish."""


def build_station_attempts(
    samples: Iterable[tuple[int, dict[str, str]]], *, run_id: str, route_id: str,
    route_sha256: str, station_ids: tuple[str, ...], bag_start_ns: int,
    bag_end_ns: int, run_status: str, expected_execution: dict | None = None,
) -> list[dict]:
    """Fold heartbeats into stable attempts with conservative MCAP evidence bounds."""
    if (not station_ids or len(set(station_ids)) != len(station_ids)
            or any(not IDENTIFIER.fullmatch(x) for x in (run_id, route_id, *station_ids))):
        raise StationEvidenceError("run, route and station identifiers must be unique and valid")
    if not 0 <= bag_start_ns <= bag_end_ns:
        raise StationEvidenceError("invalid bag time range")
    if run_status not in {
        "completed", "completed_with_exceptions", "incomplete", "failed", "aborted",
    }:
        raise StationEvidenceError("station export requires a finalized run")
    attempts = []
    counts: Counter = Counter()
    execution_id = ""
    execution_started_ns = 0
    outcomes: list[dict] = []
    pending = ""
    closed = False
    last_stamp = bag_start_ns
    last_state = ""
    seen_executions: set[str] = set()

    def append(station: str, result: str, stamp: int, bounds: str) -> None:
        if len(attempts) >= 10000:
            raise StationEvidenceError("station evidence exceeds 10000 attempts")
        counts[station] += 1
        attempts.append({
            "attempt_id": uuid.uuid5(
                uuid.NAMESPACE_URL, json.dumps([run_id, execution_id, station]),
            ).hex,
            "run_id": run_id, "execution_id": execution_id, "station_id": station,
            "attempt_seq": counts[station], "result": result,
            "reason_code": {
                "success": None, "skipped": "operator_skipped",
                "failed": "route_interrupted",
            }[result],
            "mcap_range": {
                "bag_path": f"bag/{run_id}",
                "start_ns": max(bag_start_ns, execution_started_ns), "end_ns": stamp,
                "clock": "rosbag_receive_time", "bounds_kind": bounds,
            },
        })

    for stamp, values in samples:
        if type(stamp) is not int or not last_stamp <= stamp <= bag_end_ns:
            raise StationEvidenceError("route evidence timestamps must be ordered within the bag")
        last_stamp = stamp
        if (values.get("route_id"), values.get("route_sha256")) != (
            route_id, route_sha256,
        ):
            continue
        incoming_id = values.get("execution_id", "")
        if not incoming_id:
            continue
        if not IDENTIFIER.fullmatch(incoming_id):
            raise StationEvidenceError("invalid route execution identifier")
        try:
            started = int(values["execution_started_ns"])
        except (KeyError, ValueError, TypeError) as exc:
            raise StationEvidenceError("invalid execution timestamp") from exc
        if started > stamp or started < 0:
            raise StationEvidenceError("execution timestamp is outside the recorded window")
        if started < execution_started_ns or started < bag_start_ns:
            continue
        if incoming_id != execution_id:
            if started == execution_started_ns:
                raise StationEvidenceError("ambiguous route execution identity")
            if incoming_id in seen_executions:
                raise StationEvidenceError("route execution identity was reused")
            seen_executions.add(incoming_id)
            if pending and not closed:
                append(pending, "failed", stamp, "next_execution_without_result")
            execution_id = incoming_id
            execution_started_ns = started
            outcomes = []
            closed = False
        elif started != execution_started_ns:
            raise StationEvidenceError("execution identity has contradictory timestamps")
        try:
            raw_outcomes = values["station_outcomes"]
            if len(raw_outcomes) > 262144:
                raise StationEvidenceError("station outcomes exceed 256 KiB")
            incoming = json.loads(raw_outcomes)
        except (KeyError, TypeError, ValueError) as exc:
            raise StationEvidenceError("invalid station outcomes JSON") from exc
        if (not isinstance(incoming, list) or len(incoming) > len(station_ids)
                or incoming[:len(outcomes)] != outcomes):
            raise StationEvidenceError("station outcome history is missing or contradictory")
        for index, item in enumerate(incoming):
            if (not isinstance(item, dict) or set(item) != {"station_id", "outcome"}
                    or item["station_id"] != station_ids[index]
                    or item["outcome"] not in {"completed", "skipped"}):
                raise StationEvidenceError("station outcomes must follow the frozen route")
        state = values.get("route_state", "")
        if state not in ROUTE_EXECUTION_STATES:
            raise StationEvidenceError("invalid route state")
        expected_station = station_ids[len(incoming)] if len(incoming) < len(station_ids) else ""
        if values.get("station_id", "") != expected_station:
            raise StationEvidenceError("current station contradicts the frozen route")
        if state in {"completed", "completed_with_exceptions"}:
            has_skip = any(x["outcome"] == "skipped" for x in incoming)
            if expected_station or (state == "completed_with_exceptions") != has_skip:
                raise StationEvidenceError("route completion contradicts station outcomes")
        if closed:
            if incoming != outcomes or state != last_state:
                raise StationEvidenceError("closed execution changed its result")
            continue
        for outcome in incoming[len(outcomes):]:
            station = outcome["station_id"]
            skipped = outcome["outcome"] == "skipped"
            append(station, "skipped" if skipped else "success", stamp,
                   "execution_to_result_status")
        outcomes = incoming
        last_state = state
        pending = values.get("station_id", "")
        if values.get("route_state") == "canceled":
            if pending:
                append(pending, "failed", stamp, "execution_to_result_status")
            closed = True
            pending = ""
        elif state in {"completed", "completed_with_exceptions"}:
            closed = True
    if pending and not closed:
        append(pending, "failed", bag_end_ns, "recording_end_without_result")
    if not attempts:
        raise StationEvidenceError("no station attempts from this run's frozen route")
    if expected_execution is not None:
        for key, actual in {
            "execution_id": execution_id, "execution_started_ns": execution_started_ns,
            "state": last_state, "attempts": len(seen_executions),
        }.items():
            if expected_execution.get(key) != actual:
                raise StationEvidenceError(f"recorded route {key} contradicts the run manifest")
    if run_status in {"completed", "completed_with_exceptions"}:
        if last_state not in {"completed", "completed_with_exceptions"}:
            raise StationEvidenceError("completed run has an unfinished route")
        exceptions = any(x["result"] != "success" or x["attempt_seq"] > 1 for x in attempts)
        if (run_status == "completed_with_exceptions") != exceptions:
            raise StationEvidenceError(
                "run completion contradicts station failures, skips or retries"
            )
    return attempts
