"""Tests for inspection manifest helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import jsonschema

from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
from inspection_pipeline.inspection_manager import (
    camera_diagnostic_is_ready,
    storage_writer_available,
)
from inspection_pipeline.run_manifest import (
    build_manifest,
    config_hash_from_path,
    now_utc,
    route_status_matches_snapshot,
    stop_outcome_from_route,
    update_route_execution,
    write_manifest,
)


@patch(
    "inspection_pipeline.inspection_manager.rosbag2_py.get_registered_writers",
    return_value={"sqlite3"},
)
def test_missing_mcap_writer_is_observable(_mock_writers: object) -> None:
    """The start service must not pretend an unavailable writer can record."""
    assert storage_writer_available("mcap") is False
    assert storage_writer_available("sqlite3") is True


def test_production_camera_gate_requires_complete_ready_diagnostic() -> None:
    status = DiagnosticStatus()
    status.name = "hik_camera_ros2"
    status.level = DiagnosticStatus.OK
    status.values = [
        KeyValue(key="connected", value="true"),
        KeyValue(key="calibration_state", value="ready"),
        KeyValue(key="camera_info_valid", value="true"),
    ]

    assert camera_diagnostic_is_ready(status, "hik_camera_ros2") is True
    status.values[-1].value = "false"
    assert camera_diagnostic_is_ready(status, "hik_camera_ros2") is False


def test_config_hash_is_stable(tmp_path: Path) -> None:
    """Config hashing should be deterministic for the same file."""
    config_path = tmp_path / "system.yaml"
    config_path.write_text("scout_bringup:\n  ros__parameters: {}\n", encoding="utf-8")
    first = config_hash_from_path(config_path)
    second = config_hash_from_path(config_path)
    assert first == second
    assert len(first) == 64


@patch("inspection_pipeline.run_manifest.git_commit", return_value="deadbeef")
def test_manifest_schema_fields_present(_mock_git: object, tmp_path: Path) -> None:
    """Manifest writer should include required schema fields."""
    started_at = now_utc()
    manifest = build_manifest(
        run_id="abc123",
        status="completed",
        started_at=started_at,
        ended_at=started_at,
        config_path=tmp_path / "missing.yaml",
        run_dir=tmp_path,
        storage_id="mcap",
        exit_reason="stopped_by_service",
    )
    write_manifest(tmp_path / "manifest.json", manifest)

    loaded = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "1.0"
    assert loaded["run_id"] == "abc123"
    assert loaded["status"] == "completed"
    assert loaded["bag"]["storage_id"] == "mcap"
    assert loaded["exit_reason"] == "stopped_by_service"


@patch("inspection_pipeline.run_manifest.git_commit", return_value="deadbeef")
def test_incomplete_manifest_is_not_marked_completed(_mock_git: object, tmp_path: Path) -> None:
    """Incomplete runs must remain traceable as incomplete."""
    started_at = now_utc()
    manifest = build_manifest(
        run_id="run123",
        status="incomplete",
        started_at=started_at,
        ended_at=started_at,
        config_path=tmp_path / "system.yaml",
        run_dir=tmp_path,
        storage_id="mcap",
        exit_reason="node_shutdown",
    )
    write_manifest(tmp_path / "manifest.json", manifest)
    loaded = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert loaded["status"] == "incomplete"
    assert loaded["exit_reason"] == "node_shutdown"


@patch("inspection_pipeline.run_manifest.git_commit", return_value="deadbeef")
def test_manifest_with_camera_calibration_matches_schema(
    _mock_git: object,
    tmp_path: Path,
) -> None:
    """Calibration provenance must remain valid operational-manifest data."""
    started_at = now_utc()
    manifest = build_manifest(
        run_id="run123",
        status="completed",
        started_at=started_at,
        ended_at=started_at,
        config_path=tmp_path / "system.yaml",
        run_dir=tmp_path,
        storage_id="mcap",
        exit_reason="stopped_by_service",
        camera_calibration={
            "source_url": "file:///data/calibration/camera.yaml",
            "path": "config/camera_calibration.yaml",
            "sha256": "a" * 64,
            "target_id": "a2-checkerboard-9x6-50mm-v1",
            "target_pattern": "checkerboard",
            "target_verified_at": "2026-08-24T08:00:00Z",
            "target_source_url": "file:///data/calibration/target.yaml",
            "target_path": "config/camera_calibration_target.yaml",
            "target_sha256": "b" * 64,
            "profile_id": "hik-cs050-roi-v1",
            "profile_source_url": "file:///data/calibration/camera.profile.yaml",
            "profile_path": "config/camera_calibration_profile.yaml",
            "profile_sha256": "c" * 64,
            "identity_sha256": "d" * 64,
            "camera_model": "MV-CS050-10GC",
            "camera_serial": "DA1234567",
            "lens_id": "lens-01-focus-locked-v1",
        },
    )
    schema_path = Path(__file__).resolve().parents[3] / "docs/manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator(schema).validate(manifest)


@patch("inspection_pipeline.run_manifest.git_commit", return_value="deadbeef")
def test_manifest_with_recording_evidence_matches_schema(
    _mock_git: object,
    tmp_path: Path,
) -> None:
    started_at = now_utc()
    manifest = build_manifest(
        run_id="run123",
        status="completed",
        started_at=started_at,
        ended_at=started_at,
        config_path=tmp_path / "system.yaml",
        run_dir=tmp_path,
        storage_id="mcap",
        exit_reason="route_completed",
        recording={
            "profile_id": "routine",
            "config_path": "config/recording.yaml",
            "config_sha256": "a" * 64,
            "storage_config_path": "config/mcap_writer_options.yaml",
            "storage_config_sha256": "b" * 64,
            "recorder_exit_code": 0,
            "log_path": "recording/rosbag.log",
        },
        bag_summary={
            "size_bytes": 1234,
            "file_count": 2,
            "message_count": 50,
        },
    )
    schema_path = Path(__file__).resolve().parents[3] / "docs/manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator(schema).validate(manifest)


@patch("inspection_pipeline.run_manifest.git_commit", return_value="deadbeef")
def test_manifest_with_fixed_route_matches_schema(
    _mock_git: object,
    tmp_path: Path,
) -> None:
    """The route snapshot remains linked to the operational manifest."""
    started_at = now_utc()
    manifest = build_manifest(
        run_id="run123",
        status="completed",
        started_at=started_at,
        ended_at=started_at,
        config_path=tmp_path / "system.yaml",
        run_dir=tmp_path,
        storage_id="mcap",
        exit_reason="stopped_by_service",
        fixed_route={
            "route_id": "route-a-v1",
            "source_path": "/data/scout_routes/route-a-v1.yaml",
            "path": "config/fixed_route.yaml",
            "sha256": "b" * 64,
            "verified": True,
        },
    )
    schema_path = Path(__file__).resolve().parents[3] / "docs/manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator(schema).validate(manifest)


def test_route_terminal_state_controls_inspection_stop_outcome() -> None:
    """Skipping or abandoning a route cannot be recorded as full success."""
    assert stop_outcome_from_route(None) == ("completed", "stopped_by_service")
    assert stop_outcome_from_route(None, route_required=True) == (
        "incomplete",
        "route_not_started",
    )
    assert stop_outcome_from_route(
        {"state": "completed", "attempts": 1, "reason": "completed"}
    ) == ("completed", "route_completed")
    assert stop_outcome_from_route(
        {
            "state": "completed_with_exceptions",
            "attempts": 1,
            "reason": "completed_with_exceptions",
        }
    ) == (
        "completed_with_exceptions",
        "route_completed_with_exceptions",
    )
    assert stop_outcome_from_route(
        {"state": "paused", "attempts": 1, "reason": "obstacle_stop"}
    ) == ("incomplete", "route_paused:obstacle_stop")
    assert stop_outcome_from_route(
        {"state": "completed", "attempts": 2, "reason": "completed"}
    ) == ("completed_with_exceptions", "route_completed")


def test_route_status_must_match_the_frozen_route_snapshot() -> None:
    """A stale or foreign orchestrator cannot supply this run's route outcome."""
    snapshot = {
        "route_id": "route-a-v1",
        "sha256": "a" * 64,
    }
    assert route_status_matches_snapshot(
        snapshot,
        route_id="route-a-v1",
        route_sha256="a" * 64,
    )
    assert not route_status_matches_snapshot(
        snapshot,
        route_id="route-b-v1",
        route_sha256="a" * 64,
    )
    assert not route_status_matches_snapshot(
        snapshot,
        route_id="route-a-v1",
        route_sha256="b" * 64,
    )
    assert not route_status_matches_snapshot(
        None,
        route_id="route-a-v1",
        route_sha256="a" * 64,
    )


def test_route_execution_tracking_rejects_previous_runs_and_counts_retries() -> None:
    """Late status samples cannot replace the current run's latest route attempt."""
    current, attempts = update_route_execution(
        None,
        0,
        run_started_ns=100,
        execution_id="old",
        execution_started_ns=99,
        state="completed",
        reason="completed",
    )
    assert current is None
    assert attempts == 0

    current, attempts = update_route_execution(
        current,
        attempts,
        run_started_ns=100,
        execution_id="attempt-1",
        execution_started_ns=101,
        state="paused",
        reason="obstacle_stop",
    )
    assert attempts == 1
    assert current["state"] == "paused"

    current, attempts = update_route_execution(
        current,
        attempts,
        run_started_ns=100,
        execution_id="attempt-2",
        execution_started_ns=102,
        state="completed",
        reason="completed",
    )
    assert attempts == 2
    assert current["execution_id"] == "attempt-2"
    assert current["attempts"] == 2

    late, late_attempts = update_route_execution(
        current,
        attempts,
        run_started_ns=100,
        execution_id="attempt-1",
        execution_started_ns=101,
        state="executing",
        reason="executing",
    )
    assert late == current
    assert late_attempts == attempts


@patch("inspection_pipeline.run_manifest.git_commit", return_value="deadbeef")
def test_manifest_with_route_execution_matches_schema(
    _mock_git: object,
    tmp_path: Path,
) -> None:
    """The final route attempt and retry count remain visible after recording."""
    started_at = now_utc()
    manifest = build_manifest(
        run_id="run123",
        status="completed_with_exceptions",
        started_at=started_at,
        ended_at=started_at,
        config_path=tmp_path / "system.yaml",
        run_dir=tmp_path,
        storage_id="mcap",
        exit_reason="route_completed_with_exceptions",
        route_execution={
            "execution_id": "routeexec001",
            "execution_started_ns": 1_787_472_000_000_000_000,
            "state": "completed_with_exceptions",
            "reason": "completed_with_exceptions",
            "attempts": 1,
        },
    )
    schema_path = Path(__file__).resolve().parents[3] / "docs/manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator(schema).validate(manifest)
