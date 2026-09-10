"""End-to-end tests for finalized-run trajectory export."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest
import rosbag2_py
import yaml
from nav_msgs.msg import Odometry
from rclpy.serialization import serialize_message

from inspection_pipeline.trajectory import TrajectoryError
from inspection_pipeline.trajectory_export import export_trajectory, load_scene_alignment


def sha256(path: Path) -> str:
    """Return a file SHA-256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def alignment_document(*, verified: bool = True) -> dict:
    """Return an explicit synthetic alignment for software-only tests."""
    half_angle = -math.pi / 4
    return {
        "schema_version": "1.0",
        "alignment_id": "synthetic-alignment-v1",
        "source_kind": "synthetic_contract_fixture",
        "verified": verified,
        "verified_at": "2026-08-23T08:00:00Z",
        "method": "synthetic_rigid_transform",
        "source_reference": "test-fixture",
        "input_frame": "map",
        "output_coordinate_system": "scene_local_yup",
        "transform": {
            "translation_m": [10.0, 20.0, 30.0],
            "rotation_xyzw": [
                math.sin(half_angle),
                0.0,
                0.0,
                math.cos(half_angle),
            ],
        },
        "quality": {
            "max_position_residual_m": 0.01,
            "acceptance_position_m": 0.05,
            "max_heading_residual_deg": 0.5,
            "acceptance_heading_deg": 2.0,
            "max_up_axis_error_deg": 0.5,
            "acceptance_up_axis_error_deg": 2.0,
        },
    }


def write_odometry(
    writer: rosbag2_py.SequentialWriter,
    topic: str,
    stamp_ns: int,
    *,
    frame_id: str,
    child_frame_id: str,
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    speed_mps: float = 0.0,
) -> None:
    """Write one serialized Odometry message into the synthetic MCAP."""
    message = Odometry()
    message.header.stamp.sec, message.header.stamp.nanosec = divmod(
        stamp_ns, 1_000_000_000
    )
    message.header.frame_id = frame_id
    message.child_frame_id = child_frame_id
    message.pose.pose.position.x = position[0]
    message.pose.pose.position.y = position[1]
    message.pose.pose.position.z = position[2]
    message.pose.pose.orientation.w = 1.0
    message.twist.twist.linear.x = speed_mps
    writer.write(topic, serialize_message(message), stamp_ns)


def create_finalized_run(
    tmp_path: Path,
    *,
    extrinsics_verified: bool = True,
    storage_id: str = "sqlite3",
) -> Path:
    """Create a minimal finalized run containing real rosbag2 records."""
    run_id = "run-trajectory-001"
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    bag_dir = run_dir / "bag" / run_id
    config_dir.mkdir(parents=True)
    (run_dir / "replay").mkdir()

    system = {
        "scout_bringup": {
            "ros__parameters": {
                "base_frame": "base_link",
                "body_frame": "body",
                "extrinsics_verified": extrinsics_verified,
                "base_to_body_xyz_m": [1.0, 0.0, 0.0],
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
    operational = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": "completed",
        "started_at": "2026-08-23T08:00:00Z",
        "ended_at": "2026-08-23T08:00:02Z",
        "git_commit": "deadbeef",
        "config_sha256": sha256(system_path),
        "model_version": None,
        "bag": {"storage_id": storage_id, "path": str(bag_dir)},
        "artifacts": [],
        "exit_reason": "stopped_by_service",
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(operational), encoding="utf-8"
    )

    writer = rosbag2_py.SequentialWriter()
    writer.open(
        rosbag2_py.StorageOptions(uri=str(bag_dir), storage_id=storage_id),
        rosbag2_py.ConverterOptions("", ""),
    )
    for topic in ("/Odometry", "/odom"):
        writer.create_topic(
            rosbag2_py.TopicMetadata(
                name=topic,
                type="nav_msgs/msg/Odometry",
                serialization_format="cdr",
            )
        )
    first = 1_787_472_000_000_000_000
    write_odometry(
        writer,
        "/Odometry",
        first,
        frame_id="camera_init",
        child_frame_id="body",
        position=(2.0, 3.0, 4.0),
    )
    write_odometry(
        writer,
        "/odom",
        first + 20_000_000,
        frame_id="odom",
        child_frame_id="base_link",
        speed_mps=0.2,
    )
    write_odometry(
        writer,
        "/Odometry",
        first + 1_000_000_000,
        frame_id="camera_init",
        child_frame_id="body",
        position=(3.0, 3.0, 4.0),
    )
    del writer
    return run_dir


def write_alignment(tmp_path: Path, document: dict) -> Path:
    """Write a YAML alignment fixture."""
    path = tmp_path / "alignment.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_exports_trajectory_and_provenance_from_real_rosbag(tmp_path: Path) -> None:
    """The public workflow consumes rosbag2 MCAP and is idempotent."""
    run_dir = create_finalized_run(tmp_path)
    alignment = write_alignment(tmp_path, alignment_document())

    output = export_trajectory(
        run_dir,
        alignment,
        max_speed_skew_ms=100,
        allow_synthetic_alignment=True,
        allow_non_mcap_bag=True,
    )
    repeated = export_trajectory(
        run_dir,
        alignment,
        max_speed_skew_ms=100,
        allow_synthetic_alignment=True,
        allow_non_mcap_bag=True,
    )

    assert repeated == output == run_dir / "replay/trajectory.json"
    trajectory = json.loads(output.read_text(encoding="utf-8"))
    assert len(trajectory["points"]) == 2
    assert trajectory["points"][0]["position"] == pytest.approx(
        {"x": 11.0, "y": 24.0, "z": 27.0}
    )
    assert trajectory["points"][0]["speed_mps"] == pytest.approx(0.2)
    assert trajectory["points"][1]["speed_mps"] is None
    provenance = json.loads(
        (run_dir / "replay/trajectory.provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["point_count"] == 2
    assert provenance["bag_storage_id"] == "sqlite3"
    assert provenance["alignment"]["source_kind"] == (
        "synthetic_contract_fixture"
    )
    assert provenance["trajectory_sha256"] == sha256(output)
    assert (run_dir / "config/scene_alignment.yaml").read_bytes() == (
        alignment.read_bytes()
    )


@pytest.mark.skipif(
    "mcap" not in rosbag2_py.get_registered_writers(),
    reason="rosbag2_storage_mcap is not installed",
)
def test_exports_pinned_mcap_without_test_overrides(tmp_path: Path) -> None:
    """The production path reads actual MCAP with no synthetic storage bypass."""
    run_dir = create_finalized_run(tmp_path, storage_id="mcap")
    document = alignment_document()
    document["source_kind"] = "onsite_verified"
    alignment = write_alignment(tmp_path, document)

    output = export_trajectory(run_dir, alignment)

    provenance = json.loads(
        (run_dir / "replay/trajectory.provenance.json").read_text(encoding="utf-8")
    )
    assert output.is_file()
    assert provenance["bag_storage_id"] == "mcap"


def test_real_workflow_rejects_synthetic_alignment_by_default(tmp_path: Path) -> None:
    """A software fixture cannot silently become on-site alignment evidence."""
    run_dir = create_finalized_run(tmp_path)
    alignment = write_alignment(tmp_path, alignment_document())

    with pytest.raises(TrajectoryError, match="synthetic scene alignment"):
        export_trajectory(run_dir, alignment, allow_non_mcap_bag=True)


def test_real_workflow_rejects_non_mcap_bag_by_default(tmp_path: Path) -> None:
    """Non-MCAP fixtures cannot silently replace the pinned run format."""
    run_dir = create_finalized_run(tmp_path)
    document = alignment_document()
    document["source_kind"] = "onsite_verified"
    alignment = write_alignment(tmp_path, document)

    with pytest.raises(TrajectoryError, match="requires MCAP"):
        export_trajectory(run_dir, alignment)


def test_export_rejects_unverified_extrinsics(tmp_path: Path) -> None:
    """Placeholder base-to-body geometry cannot produce a deliverable track."""
    run_dir = create_finalized_run(tmp_path, extrinsics_verified=False)
    alignment = write_alignment(tmp_path, alignment_document())

    with pytest.raises(TrajectoryError, match="extrinsics_verified"):
        export_trajectory(
            run_dir,
            alignment,
            allow_synthetic_alignment=True,
            allow_non_mcap_bag=True,
        )


def test_alignment_quality_gate_rejects_residual_over_limit(tmp_path: Path) -> None:
    """A marked-verified file still fails when its measured quality misses the gate."""
    document = alignment_document()
    document["quality"]["max_position_residual_m"] = 0.2
    alignment = write_alignment(tmp_path, document)

    with pytest.raises(TrajectoryError, match="position residual"):
        load_scene_alignment(alignment, allow_synthetic=True)


def test_alignment_rotation_must_really_produce_y_up(tmp_path: Path) -> None:
    """A mislabeled identity rotation cannot claim scene-local Y-up coordinates."""
    document = alignment_document()
    document["transform"]["rotation_xyzw"] = [0.0, 0.0, 0.0, 1.0]
    alignment = write_alignment(tmp_path, document)

    with pytest.raises(TrajectoryError, match=r"map \+Z to scene \+Y"):
        load_scene_alignment(alignment, allow_synthetic=True)
