"""End-to-end tests for bounded offline point-cloud export."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest
import rosbag2_py
import yaml
from rclpy.serialization import serialize_message
from sensor_msgs.msg import PointField
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header

from inspection_pipeline.pointcloud import PointCloudError, read_pcd_header
from inspection_pipeline.pointcloud_export import export_pointcloud


def sha256(path: Path) -> str:
    """Return one fixture digest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def alignment_document(*, source_kind: str = "synthetic_contract_fixture") -> dict:
    """Return a Y-up rigid alignment with explicit test provenance."""
    half_angle = -math.pi / 4
    return {
        "schema_version": "1.0",
        "alignment_id": "synthetic-alignment-v1",
        "source_kind": source_kind,
        "verified": True,
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


def export_settings(**overrides: object) -> dict:
    """Return small deterministic limits for synthetic bags."""
    settings = {
        "schema_version": "1.0",
        "source_topic": "/cloud_registered",
        "chunk_duration_ns": 5_000_000_000,
        "voxel_leaf_m": 1.0,
        "max_input_points_per_message": 100,
        "max_input_points_per_chunk": 200,
        "max_output_points_per_chunk": 100,
        "max_chunks": 4,
        "max_duration_ns": 30_000_000_000,
        "max_abs_coordinate_m": 1000.0,
    }
    settings.update(overrides)
    return settings


def write_cloud(
    writer: rosbag2_py.SequentialWriter,
    stamp_ns: int,
    points: list[tuple[float, float, float, float]],
    *,
    frame_id: str = "camera_init",
) -> None:
    """Write one production-shaped XYZI cloud."""
    header = Header()
    header.stamp.sec, header.stamp.nanosec = divmod(stamp_ns, 1_000_000_000)
    header.frame_id = frame_id
    fields = [
        PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
        PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
        PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
        PointField(name="intensity", offset=12, datatype=PointField.FLOAT32, count=1),
    ]
    message = point_cloud2.create_cloud(header, fields, points)
    writer.write("/cloud_registered", serialize_message(message), stamp_ns)


def create_finalized_run(
    tmp_path: Path,
    *,
    storage_id: str = "sqlite3",
    second_frame: str = "camera_init",
    include_nan: bool = False,
) -> tuple[Path, Path, Path]:
    """Create a finalized run containing two real PointCloud2 bag records."""
    run_id = "run-pointcloud-001"
    run_dir = tmp_path / "run"
    config_dir = run_dir / "config"
    bag_dir = run_dir / "bag" / run_id
    config_dir.mkdir(parents=True)
    (run_dir / "artifacts").mkdir()
    (run_dir / "replay").mkdir()
    system = {
        "scout_bringup": {
            "ros__parameters": {
                "base_frame": "base_link",
                "body_frame": "body",
                "extrinsics_verified": True,
                "base_to_body_xyz_m": [0.0, 0.0, 0.0],
                "base_to_body_rpy_rad": [0.0, 0.0, 0.0],
                "fast_lio_config_file": "mid360.yaml",
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
        "health_monitor": {"ros__parameters": {"scout_odom_topic": "/odom"}},
    }
    system_path = config_dir / "system.yaml"
    system_path.write_text(yaml.safe_dump(system, sort_keys=False), encoding="utf-8")
    (config_dir / "fast_lio.yaml").write_text(
        "/**:\n  ros__parameters:\n    publish:\n      scan_publish_en: true\n",
        encoding="utf-8",
    )
    operational = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": "completed",
        "started_at": "2026-08-23T08:00:00Z",
        "ended_at": "2026-08-23T08:00:08Z",
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
    writer.create_topic(
        rosbag2_py.TopicMetadata(
            name="/cloud_registered",
            type="sensor_msgs/msg/PointCloud2",
            serialization_format="cdr",
        )
    )
    first = 1_787_472_000_000_000_000
    first_points = [(0.1, 0.0, 0.0, 1.0), (0.2, 0.0, 0.0, 3.0)]
    if include_nan:
        first_points.append((float("nan"), 0.0, 0.0, 2.0))
    write_cloud(writer, first, first_points)
    write_cloud(
        writer,
        first + 6_000_000_000,
        [(2.1, 0.0, 0.0, 4.0), (4.1, 0.0, 0.0, 5.0)],
        frame_id=second_frame,
    )
    del writer
    alignment = tmp_path / "alignment.yaml"
    alignment.write_text(
        yaml.safe_dump(alignment_document(), sort_keys=False), encoding="utf-8"
    )
    settings = tmp_path / "pointcloud_export.yaml"
    settings.write_text(
        yaml.safe_dump(export_settings(), sort_keys=False), encoding="utf-8"
    )
    return run_dir, alignment, settings


def test_exports_deterministic_scene_aligned_pcd_chunks(tmp_path: Path) -> None:
    """The public workflow reads a real bag and atomically publishes fixed windows."""
    run_dir, alignment, settings = create_finalized_run(tmp_path)

    output = export_pointcloud(
        run_dir,
        alignment,
        export_config_path=settings,
        allow_synthetic_alignment=True,
        allow_non_mcap_bag=True,
    )
    repeated = export_pointcloud(
        run_dir,
        alignment,
        export_config_path=settings,
        allow_synthetic_alignment=True,
        allow_non_mcap_bag=True,
    )

    assert repeated == output == run_dir / "artifacts/pointcloud/manifest.json"
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["source"]["message_count"] == 2
    assert manifest["source"]["input_point_count"] == 4
    assert [chunk["window_index"] for chunk in manifest["chunks"]] == [0, 1]
    assert [chunk["output_point_count"] for chunk in manifest["chunks"]] == [1, 2]
    assert manifest["chunks"][0]["bounds_m"]["min"] == pytest.approx(
        [10.15, 20.0, 30.0]
    )
    for sequence, expected_points in enumerate((1, 2)):
        header = read_pcd_header(
            run_dir / f"artifacts/pointcloud/chunks/{sequence:06d}.pcd"
        )
        assert header.points == expected_points
    assert (run_dir / "config/scene_alignment.yaml").read_bytes() == alignment.read_bytes()
    assert (run_dir / "config/pointcloud_export.yaml").read_bytes() == settings.read_bytes()
    assert not list((run_dir / "artifacts").glob(".pointcloud.*"))


@pytest.mark.skipif(
    "mcap" not in rosbag2_py.get_registered_writers(),
    reason="rosbag2_storage_mcap is not installed",
)
def test_exports_pinned_mcap_without_storage_override(tmp_path: Path) -> None:
    """The production path consumes actual MCAP storage."""
    run_dir, alignment, _settings = create_finalized_run(tmp_path, storage_id="mcap")
    document = alignment_document(source_kind="onsite_verified")
    alignment.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    output = export_pointcloud(run_dir, alignment)

    assert output.is_file()
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["source"]["bag_storage_id"] == "mcap"


def test_rejects_mixed_frames_without_publishing_partial_output(tmp_path: Path) -> None:
    """A frame change cannot silently merge unrelated coordinate systems."""
    run_dir, alignment, settings = create_finalized_run(
        tmp_path, second_frame="other_frame"
    )

    with pytest.raises(PointCloudError, match="does not match"):
        export_pointcloud(
            run_dir,
            alignment,
            export_config_path=settings,
            allow_synthetic_alignment=True,
            allow_non_mcap_bag=True,
        )

    assert not (run_dir / "artifacts/pointcloud").exists()
    assert not list((run_dir / "artifacts").glob(".pointcloud.*"))


def test_rejects_nonfinite_points_and_resource_overflow(tmp_path: Path) -> None:
    """Corrupt points and configured hard-limit breaches fail closed."""
    run_dir, alignment, settings = create_finalized_run(tmp_path, include_nan=True)
    with pytest.raises(PointCloudError, match="NaN or Inf"):
        export_pointcloud(
            run_dir,
            alignment,
            export_config_path=settings,
            allow_synthetic_alignment=True,
            allow_non_mcap_bag=True,
        )

    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    run_dir, alignment, settings = create_finalized_run(clean_root)
    settings.write_text(
        yaml.safe_dump(
            export_settings(
                max_input_points_per_message=1,
                max_input_points_per_chunk=1,
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(PointCloudError, match="max_input_points_per_message"):
        export_pointcloud(
            run_dir,
            alignment,
            export_config_path=settings,
            allow_synthetic_alignment=True,
            allow_non_mcap_bag=True,
        )


def test_requires_frozen_fast_lio_config_and_production_alignment(tmp_path: Path) -> None:
    """Untraceable mapping parameters or synthetic field evidence are rejected."""
    run_dir, alignment, settings = create_finalized_run(tmp_path)
    (run_dir / "config/fast_lio.yaml").unlink()
    with pytest.raises(PointCloudError, match="fast_lio.yaml"):
        export_pointcloud(
            run_dir,
            alignment,
            export_config_path=settings,
            allow_synthetic_alignment=True,
            allow_non_mcap_bag=True,
        )

    run_dir, alignment, settings = create_finalized_run(tmp_path / "second")
    with pytest.raises(PointCloudError, match="synthetic scene alignment"):
        export_pointcloud(
            run_dir,
            alignment,
            export_config_path=settings,
            allow_non_mcap_bag=True,
        )
