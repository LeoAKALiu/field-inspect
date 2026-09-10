"""Tests for the fixed-route contract and supervised state machine."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from inspection_pipeline.fixed_route import (
    FixedRouteError,
    RouteCommand,
    RouteMachine,
    RouteState,
    load_fixed_route,
    route_pose_length,
)


def route_document(*, verified: bool = True) -> dict:
    """Return a small route with two physical inspection stations."""
    return {
        "schema_version": "1.0",
        "route_id": "route-a-v1",
        "source_kind": "onsite_taught" if verified else "synthetic_contract_fixture",
        "frame_id": "map",
        "verified": verified,
        "verified_at": "2026-08-23T10:00:00Z" if verified else None,
        "source_reference": "field-record-2026-08-23" if verified else "",
        "approved_max_linear_mps": 0.2,
        "poses": [
            {"x_m": 0.0, "y_m": 0.0, "yaw_rad": 0.0},
            {"x_m": 1.0, "y_m": 0.0, "yaw_rad": 0.0},
            {"x_m": 2.0, "y_m": 0.0, "yaw_rad": 0.0},
            {"x_m": 2.0, "y_m": 1.0, "yaw_rad": 1.57},
        ],
        "stations": [
            {
                "station_id": "station-001",
                "pose_index": 1,
                "required": True,
                "tag_ids": ["tag-001"],
            },
            {
                "station_id": "station-002",
                "pose_index": 3,
                "required": True,
                "tag_ids": [],
            },
        ],
    }


def write_route(path: Path, document: dict) -> None:
    """Serialize a route fixture as YAML."""
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def load_verified_route(tmp_path: Path):
    """Load the standard verified fixture."""
    path = tmp_path / "route.yaml"
    write_route(path, route_document())
    return load_fixed_route(path)


def test_loads_verified_route_and_splits_at_physical_stations(tmp_path: Path) -> None:
    """Stations end deterministic FollowPath segments but are not themselves tags."""
    route = load_verified_route(tmp_path)

    assert route.route_id == "route-a-v1"
    assert route.stations[0].station_id == "station-001"
    assert route.stations[0].tag_ids == ("tag-001",)
    assert len(route.segment_for_station(0)) == 2
    assert len(route.segment_for_station(1)) == 3
    assert route_pose_length(route.poses) == pytest.approx(3.0)
    assert len(route.source_sha256) == 64


def test_runtime_rejects_unverified_route_but_tests_can_load_it(tmp_path: Path) -> None:
    """A commissioning fixture cannot silently authorize physical motion."""
    path = tmp_path / "route.yaml"
    write_route(path, route_document(verified=False))

    with pytest.raises(FixedRouteError, match="not verified"):
        load_fixed_route(path)
    assert load_fixed_route(path, allow_unverified=True).verified is False


def test_odometry_teaching_candidate_is_always_unverified(tmp_path: Path) -> None:
    """An automatically generated candidate cannot authorize vehicle motion."""
    document = route_document(verified=False)
    document["source_kind"] = "odometry_teaching_candidate"
    document["source_reference"] = "teaching-run:run-001"
    path = tmp_path / "route.yaml"
    write_route(path, document)

    candidate = load_fixed_route(path, allow_unverified=True)
    assert candidate.source_kind == "odometry_teaching_candidate"
    with pytest.raises(FixedRouteError, match="not verified"):
        load_fixed_route(path)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: value.update(frame_id="odom"), "frame_id must be map"),
        (
            lambda value: value.update(approved_max_linear_mps=0.21),
            "must be in",
        ),
        (
            lambda value: value["stations"][1].update(pose_index=2),
            "final route pose",
        ),
        (
            lambda value: value["poses"][1].update(x_m=float("nan")),
            "finite number",
        ),
    ],
)
def test_contract_rejects_unsafe_or_untraceable_routes(
    tmp_path: Path,
    mutator,
    message: str,
) -> None:
    """Frame, speed, station and numeric gates all fail closed."""
    document = route_document()
    mutator(document)
    path = tmp_path / "route.yaml"
    write_route(path, document)

    with pytest.raises(FixedRouteError, match=message):
        load_fixed_route(path)


def test_safety_stop_latches_pause_and_resume_replays_same_segment(tmp_path: Path) -> None:
    """Clearing a safety condition never resumes FollowPath automatically."""
    machine = RouteMachine(load_verified_route(tmp_path))

    started = machine.start()
    assert started.command == RouteCommand.SEND_SEGMENT
    assert machine.state == RouteState.ARMING
    assert machine.controller_active().state == RouteState.EXECUTING
    segment = machine.current_segment()

    stopped = machine.pause("obstacle_stop")
    assert stopped.command == RouteCommand.CANCEL_GOAL
    assert machine.state == RouteState.PAUSED
    assert machine.current_segment() == segment
    assert machine.controller_active().accepted is False

    resumed = machine.resume()
    assert resumed.command == RouteCommand.SEND_SEGMENT
    assert machine.state == RouteState.ARMING
    assert machine.current_segment() == segment


def test_station_outcomes_drive_terminal_state(tmp_path: Path) -> None:
    """Required station exceptions remain visible in the route terminal state."""
    machine = RouteMachine(load_verified_route(tmp_path))
    machine.start()
    machine.controller_active()
    assert machine.reached_station().state == RouteState.AT_STATION
    next_segment = machine.record_station(skipped=False)
    assert next_segment.command == RouteCommand.SEND_SEGMENT
    assert machine.current_station.station_id == "station-002"

    machine.controller_active()
    machine.reached_station()
    finished = machine.record_station(skipped=True)

    assert finished.state == RouteState.COMPLETED_WITH_EXCEPTIONS
    assert [item.outcome for item in machine.outcomes] == ["completed", "skipped"]


def test_stale_action_result_cannot_advance_a_paused_route(tmp_path: Path) -> None:
    """A canceled action's late success must not consume a station."""
    machine = RouteMachine(load_verified_route(tmp_path))
    machine.start()
    machine.controller_active()
    machine.pause("tf_unhealthy")

    result = machine.reached_station()

    assert result.accepted is False
    assert machine.state == RouteState.PAUSED
    assert machine.station_index == 0


def test_verified_route_requires_real_source_and_review_time(tmp_path: Path) -> None:
    """Changing only the verified flag cannot promote a synthetic fixture."""
    document = route_document(verified=False)
    document["verified"] = True
    path = tmp_path / "route.yaml"
    write_route(path, document)

    with pytest.raises(FixedRouteError, match="source_kind=onsite_taught"):
        load_fixed_route(path)


def test_duplicate_station_ids_are_rejected(tmp_path: Path) -> None:
    """Station outcomes need stable, unambiguous identifiers."""
    document = deepcopy(route_document())
    document["stations"][1]["station_id"] = "station-001"
    path = tmp_path / "route.yaml"
    write_route(path, document)

    with pytest.raises(FixedRouteError, match="station_id values must be unique"):
        load_fixed_route(path)


def test_route_rejects_customer_asset_data(tmp_path: Path) -> None:
    """Vehicle route files must not become a shadow customer asset registry."""
    document = route_document()
    document["stations"][0]["asset_ids"] = ["customer-asset-001"]
    path = tmp_path / "route.yaml"
    write_route(path, document)

    with pytest.raises(FixedRouteError, match="must contain only"):
        load_fixed_route(path)
