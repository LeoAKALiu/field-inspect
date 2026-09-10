"""End-to-end tests for deterministic fixed-route candidate generation."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path

import pytest
import rosbag2_py
import yaml
from nav_msgs.msg import Odometry
from rclpy.serialization import serialize_message

from inspection_pipeline.fixed_route import FixedRouteError, load_fixed_route
from inspection_pipeline.route_teaching import (
    PROVENANCE_FILENAME,
    ROUTE_FILENAME,
    RouteTeachingError,
    export_route_candidate,
)

CONFIG_PATH = Path(__file__).parents[1] / "config/route_teaching.yaml"


def sha256(path: Path) -> str:
    """Return a file SHA-256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def route_points() -> list[tuple[float, float, float]]:
    """Return a forward-only L-shaped teaching trace with small position noise."""
    return [
        (0.0, 0.0, 0.0),
        (0.25, 0.005, 0.0),
        (0.5, 0.0, 0.0),
        (0.75, -0.004, 0.0),
        (1.0, 0.0, math.pi / 2.0),
        (1.0, 0.25, math.pi / 2.0),
        (1.004, 0.5, math.pi / 2.0),
        (1.0, 0.75, math.pi / 2.0),
        (1.0, 1.0, math.pi / 2.0),
    ]


def write_odometry(
    writer: rosbag2_py.SequentialWriter,
    *,
    stamp_ns: int,
    x_m: float,
    y_m: float,
    yaw_rad: float,
    frame_id: str = "camera_init",
    child_frame_id: str = "body",
) -> None:
    """Write one serialized FAST-LIO-style Odometry sample."""
    message = Odometry()
    message.header.stamp.sec, message.header.stamp.nanosec = divmod(
        stamp_ns, 1_000_000_000
    )
    message.header.frame_id = frame_id
    message.child_frame_id = child_frame_id
    message.pose.pose.position.x = x_m
    message.pose.pose.position.y = y_m
    message.pose.pose.orientation.z = math.sin(yaw_rad / 2.0)
    message.pose.pose.orientation.w = math.cos(yaw_rad / 2.0)
    writer.write("/Odometry", serialize_message(message), stamp_ns)


def create_teaching_run(
    tmp_path: Path,
    *,
    storage_id: str = "sqlite3",
    status: str = "completed",
    extrinsics_verified: bool = True,
    points: list[tuple[float, float, float]] | None = None,
    frame_id: str = "camera_init",
) -> Path:
    """Create a finalized run containing real rosbag2 records."""
    run_id = "run-route-teaching-001"
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    bag_dir = run_dir / "bag" / run_id
    config_dir.mkdir(parents=True)
    system = {
        "scout_bringup": {
            "ros__parameters": {
                "base_frame": "base_link",
                "body_frame": "body",
                "extrinsics_verified": extrinsics_verified,
                "base_to_body_xyz_m": [0.0, 0.0, 0.0],
                "base_to_body_rpy_rad": [0.0, 0.0, 0.0],
            }
        },
        "localization_adapter": {
            "ros__parameters": {
                "input_odom_topic": "/Odometry",
                "input_global_frame": "camera_init",
                "body_frame": "body",
                "map_frame": "map",
                "odom_frame": "odom",
                "base_frame": "base_link",
            }
        },
        "health_monitor": {
            "ros__parameters": {
                "scout_odom_topic": "/odom",
            }
        },
    }
    system_path = config_dir / "system.yaml"
    system_path.write_text(yaml.safe_dump(system, sort_keys=False), encoding="utf-8")
    (config_dir / "fast_lio.yaml").write_text(
        "publish:\n  scan_publish_en: true\n",
        encoding="utf-8",
    )
    operational = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": status,
        "started_at": "2026-08-23T08:00:00Z",
        "ended_at": "2026-08-23T08:00:03Z",
        "git_commit": "deadbeef",
        "config_sha256": sha256(system_path),
        "model_version": None,
        "bag": {"storage_id": storage_id, "path": str(bag_dir)},
        "artifacts": [],
        "exit_reason": "stopped_by_service",
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(operational),
        encoding="utf-8",
    )
    writer = rosbag2_py.SequentialWriter()
    writer.open(
        rosbag2_py.StorageOptions(uri=str(bag_dir), storage_id=storage_id),
        rosbag2_py.ConverterOptions("", ""),
    )
    writer.create_topic(
        rosbag2_py.TopicMetadata(
            name="/Odometry",
            type="nav_msgs/msg/Odometry",
            serialization_format="cdr",
        )
    )
    first_stamp = 1_787_472_000_000_000_000
    for index, (x_m, y_m, yaw_rad) in enumerate(points or route_points()):
        write_odometry(
            writer,
            stamp_ns=first_stamp + index * 250_000_000,
            x_m=x_m,
            y_m=y_m,
            yaw_rad=yaw_rad,
            frame_id=frame_id,
        )
    del writer
    return run_dir


def teaching_spec() -> dict:
    """Return two human-entered physical station positions."""
    return {
        "schema_version": "1.0",
        "route_id": "route-taught-v1",
        "approved_max_linear_mps": 0.2,
        "stations": [
            {
                "station_id": "station-turn",
                "x_m": 1.01,
                "y_m": 0.01,
                "required": True,
                "tag_ids": ["tag-001"],
            },
            {
                "station_id": "station-final",
                "x_m": 1.0,
                "y_m": 1.0,
                "required": True,
                "tag_ids": [],
            },
        ],
    }


def write_spec(tmp_path: Path, document: dict | None = None) -> Path:
    """Write a route-teaching specification fixture."""
    path = tmp_path / "teaching-spec.yaml"
    path.write_text(
        yaml.safe_dump(document or teaching_spec(), sort_keys=False),
        encoding="utf-8",
    )
    return path


def export_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Export the standard SQLite software fixture."""
    run_dir = create_teaching_run(tmp_path)
    output = tmp_path / "candidate"
    route = export_route_candidate(
        run_dir,
        write_spec(tmp_path),
        output,
        teaching_config_path=CONFIG_PATH,
        allow_non_mcap_bag=True,
    )
    return route, output


def test_exports_unverified_candidate_report_and_is_idempotent(tmp_path: Path) -> None:
    """The public workflow simplifies, resamples and reports each fixed station."""
    route_path, output = export_fixture(tmp_path)
    repeated = export_route_candidate(
        tmp_path / "run",
        tmp_path / "teaching-spec.yaml",
        output,
        teaching_config_path=CONFIG_PATH,
        allow_non_mcap_bag=True,
    )

    assert route_path == repeated == output / ROUTE_FILENAME
    route = yaml.safe_load(route_path.read_text(encoding="utf-8"))
    assert route["source_kind"] == "odometry_teaching_candidate"
    assert route["verified"] is False
    assert route["verified_at"] is None
    assert route["source_reference"] == "teaching-run:run-route-teaching-001"
    assert len(route["poses"]) > 10
    assert route["stations"][0]["tag_ids"] == ["tag-001"]
    assert route["stations"][-1]["pose_index"] == len(route["poses"]) - 1
    provenance = json.loads(
        (output / PROVENANCE_FILENAME).read_text(encoding="utf-8")
    )
    assert provenance["candidate"]["sha256"] == sha256(route_path)
    assert provenance["candidate"]["route_length_m"] == pytest.approx(2.0, abs=0.05)
    assert provenance["processing"]["backend"].startswith("Shapely")
    assert provenance["teaching_spec"]["stations"][0][
        "candidate_pose_index"
    ] == route["stations"][0]["pose_index"]
    assert all(item["sha256"] for item in provenance["source"]["bag_files"])
    with pytest.raises(FixedRouteError, match="not verified"):
        load_fixed_route(route_path)


def test_candidate_cannot_be_promoted_by_flipping_verified_only(tmp_path: Path) -> None:
    """Promotion also requires an onsite source kind and review evidence."""
    route_path, _output = export_fixture(tmp_path)
    document = yaml.safe_load(route_path.read_text(encoding="utf-8"))
    document["verified"] = True
    document["verified_at"] = "2026-08-23T10:00:00Z"
    promoted = tmp_path / "unsafe-promotion.yaml"
    promoted.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(FixedRouteError, match="source_kind=onsite_taught"):
        load_fixed_route(promoted)


def test_spec_rejects_customer_fields_without_partial_output(tmp_path: Path) -> None:
    """The candidate spec cannot become a customer asset or instrument registry."""
    run_dir = create_teaching_run(tmp_path)
    document = teaching_spec()
    document["stations"][0]["asset_ids"] = ["customer-device-1"]
    output = tmp_path / "candidate"

    with pytest.raises(RouteTeachingError, match="must contain only"):
        export_route_candidate(
            run_dir,
            write_spec(tmp_path, document),
            output,
            teaching_config_path=CONFIG_PATH,
            allow_non_mcap_bag=True,
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".candidate.*"))

    with pytest.raises(RouteTeachingError, match="outside the immutable"):
        export_route_candidate(
            run_dir,
            write_spec(tmp_path, teaching_spec()),
            run_dir / "replay/candidate",
            teaching_config_path=CONFIG_PATH,
            allow_non_mcap_bag=True,
        )
    assert not (run_dir / "replay").exists()


def test_final_station_must_be_the_taught_route_endpoint(tmp_path: Path) -> None:
    """The exporter does not silently discard an unreviewed route tail."""
    run_dir = create_teaching_run(tmp_path)
    document = teaching_spec()
    document["stations"][-1]["x_m"] = 1.0
    document["stations"][-1]["y_m"] = 0.75

    with pytest.raises(RouteTeachingError, match="final station"):
        export_route_candidate(
            run_dir,
            write_spec(tmp_path, document),
            tmp_path / "candidate",
            teaching_config_path=CONFIG_PATH,
            allow_non_mcap_bag=True,
        )


@pytest.mark.parametrize(
    ("run_kwargs", "message"),
    [
        ({"status": "incomplete"}, "status must be completed"),
        ({"extrinsics_verified": False}, "extrinsics_verified"),
        ({"frame_id": "map"}, "parent frame"),
    ],
)
def test_rejects_untrusted_run_inputs(
    tmp_path: Path,
    run_kwargs: dict,
    message: str,
) -> None:
    """Finalization, calibrated geometry and recorded frames fail closed."""
    run_dir = create_teaching_run(tmp_path, **run_kwargs)

    with pytest.raises(RouteTeachingError, match=message):
        export_route_candidate(
            run_dir,
            write_spec(tmp_path),
            tmp_path / "candidate",
            teaching_config_path=CONFIG_PATH,
            allow_non_mcap_bag=True,
        )


def test_resource_limit_and_in_place_rotation_fail_closed(tmp_path: Path) -> None:
    """The exporter refuses truncation and unsupported stationary turns."""
    run_dir = create_teaching_run(tmp_path)
    settings = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    settings["max_source_samples"] = 2
    config = tmp_path / "route-teaching-small.yaml"
    config.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    with pytest.raises(RouteTeachingError, match="sample count"):
        export_route_candidate(
            run_dir,
            write_spec(tmp_path),
            tmp_path / "too-large",
            teaching_config_path=config,
            allow_non_mcap_bag=True,
        )

    rotated = route_points()
    rotated.insert(1, (0.0, 0.0, 1.0))
    other_root = tmp_path / "rotation"
    other_root.mkdir()
    rotation_run = create_teaching_run(other_root, points=rotated)
    with pytest.raises(RouteTeachingError, match="in-place rotation"):
        export_route_candidate(
            rotation_run,
            write_spec(other_root),
            other_root / "candidate",
            teaching_config_path=CONFIG_PATH,
            allow_non_mcap_bag=True,
        )


def test_existing_different_candidate_is_not_overwritten(tmp_path: Path) -> None:
    """A reviewed candidate directory is immutable once published."""
    _route_path, output = export_fixture(tmp_path)
    spec = deepcopy(teaching_spec())
    spec["route_id"] = "route-taught-v2"
    changed_spec = tmp_path / "changed-spec.yaml"
    changed_spec.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    with pytest.raises(RouteTeachingError, match="different content"):
        export_route_candidate(
            tmp_path / "run",
            changed_spec,
            output,
            teaching_config_path=CONFIG_PATH,
            allow_non_mcap_bag=True,
        )

    original = yaml.safe_load((output / ROUTE_FILENAME).read_text(encoding="utf-8"))
    assert original["route_id"] == "route-taught-v1"


@pytest.mark.skipif(
    "mcap" not in rosbag2_py.get_registered_writers(),
    reason="rosbag2_storage_mcap is not installed",
)
def test_reads_production_mcap_without_test_override(tmp_path: Path) -> None:
    """The production route consumes a native MCAP teaching run."""
    run_dir = create_teaching_run(tmp_path, storage_id="mcap")
    output = export_route_candidate(
        run_dir,
        write_spec(tmp_path),
        tmp_path / "candidate",
    )

    provenance = json.loads(
        (output.parent / PROVENANCE_FILENAME).read_text(encoding="utf-8")
    )
    assert provenance["source"]["bag_storage_id"] == "mcap"
    assert any(item["path"].endswith(".mcap") for item in provenance["source"]["bag_files"])
