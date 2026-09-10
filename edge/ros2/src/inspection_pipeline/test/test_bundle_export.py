"""Tests for durable RFC 8493 USB inspection-run export."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
from pathlib import Path

import pytest
import rosbag2_py
import yaml
from rclpy.serialization import serialize_message
from std_msgs.msg import String

from inspection_pipeline.bag_health import inspect_bag
from inspection_pipeline.bagit import BundleVerificationError, verify_bagit_bundle
from inspection_pipeline.bundle_export import BundleExportError, export_run_bundle
from inspection_pipeline.run_validation import validate_run_directory
from inspection_pipeline import usb_finalize as usb_finalize_module
from inspection_pipeline.usb_finalize import UsbMount, finalize_usb

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture(autouse=True)
def fake_pinned_mcap_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the doctor gate without adding a network dependency to tests."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binary = binary_dir / "mcap"
    binary.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'mcap-cli 0.3.0'; exit 0; fi\n"
        "if [ \"$1\" = \"doctor\" ]; then exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_dir}:{os.environ['PATH']}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_finalized_run(tmp_path: Path) -> Path:
    run_id = "run-20260823-001"
    run_dir = tmp_path / "run"
    (run_dir / "config").mkdir(parents=True)
    (run_dir / "bag").mkdir(parents=True)
    (run_dir / "replay").mkdir(parents=True)
    (run_dir / "artifacts").mkdir(parents=True)

    config = run_dir / "config/system.yaml"
    config.write_text("safety_mux:\n  max_linear_mps: 0.2\n", encoding="utf-8")
    (run_dir / "config/camera_calibration.yaml").write_text(
        "camera_name: hik_mv_cs050_10gc\n",
        encoding="utf-8",
    )
    alignment_path = run_dir / "config/scene_alignment.yaml"
    alignment_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "alignment_id": "alignment-fixture-v1",
                "source_kind": "onsite_verified",
                "verified": True,
                "verified_at": "2026-08-23T04:00:00Z",
                "method": "test_fixture",
                "source_reference": "test-fixture",
                "input_frame": "map",
                "output_coordinate_system": "scene_local_yup",
                "transform": {
                    "translation_m": [0.0, 0.0, 0.0],
                    "rotation_xyzw": [
                        -0.7071067811865475,
                        0.0,
                        0.0,
                        0.7071067811865476,
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
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    recording = run_dir / "config/recording.yaml"
    recording.write_text(
        "schema_version: '1.0'\n"
        "storage_id: mcap\n"
        "storage_config_file: mcap_writer_options.yaml\n"
        "profile_id: routine\n"
        "max_bagfile_size_bytes: 2147483648\n"
        "max_bagfile_duration_seconds: 300\n"
        "max_cache_size_bytes: 67108864\n"
        "max_run_size_bytes: 107374182400\n"
        "min_start_free_space_bytes: 32212254720\n"
        "min_runtime_free_space_bytes: 21474836480\n"
        "monitor_period_seconds: 1.0\n"
        "stop_timeout_seconds: 30.0\n"
        "profiles:\n"
        "  acceptance_raw:\n"
        "    storage_preset_profile: zstd_fast\n"
        "    topics: [/test]\n"
        "    required_topics: [/test]\n"
        "  routine:\n"
        "    storage_preset_profile: zstd_fast\n"
        "    topics: [/test]\n"
        "    required_topics: [/test]\n",
        encoding="utf-8",
    )
    storage_config = run_dir / "config/mcap_writer_options.yaml"
    storage_config.write_text(
        "noChunking: false\n"
        "noChunkCRC: false\n"
        "noSummaryCRC: false\n"
        "noMessageIndex: false\n"
        "noSummary: false\n"
        "compression: Zstd\n"
        "compressionLevel: Fastest\n"
        "chunkSize: 1048576\n"
        "forceCompression: false\n",
        encoding="utf-8",
    )
    (run_dir / "recording").mkdir()
    (run_dir / "recording/rosbag.log").write_text("", encoding="utf-8")
    writer = rosbag2_py.SequentialWriter()
    writer.open(
        rosbag2_py.StorageOptions(
            uri=str(run_dir / "bag" / run_id),
            storage_id="mcap",
        ),
        rosbag2_py.ConverterOptions("", ""),
    )
    writer.create_topic(
        rosbag2_py.TopicMetadata(
            name="/test",
            type="std_msgs/msg/String",
            serialization_format="cdr",
            offered_qos_profiles="",
        )
    )
    writer.write(
        "/test",
        serialize_message(String(data="fixture")),
        1_787_472_001_000_000_000,
    )
    del writer
    trajectory = {
        "schema_version": "1.0",
        "run_id": run_id,
        "coordinate_system": "scene_local_yup",
        "alignment_id": "alignment-fixture-v1",
        "points": [
            {
                "seq": 0,
                "timestamp": "2026-08-23T04:00:01Z",
                "position": {"x": 1.0, "y": 0.0, "z": 2.0},
                "heading_deg": 10.0,
                "speed_mps": 0.0,
            },
            {
                "seq": 1,
                "timestamp": "2026-08-23T04:00:02Z",
                "position": {"x": 1.2, "y": 0.0, "z": 2.1},
                "heading_deg": 10.0,
                "speed_mps": 0.2,
            },
        ],
    }
    (run_dir / "replay/trajectory.json").write_text(
        json.dumps(trajectory, allow_nan=False),
        encoding="utf-8",
    )
    trajectory_path = run_dir / "replay/trajectory.json"
    provenance = {
        "schema_version": "1.0",
        "run_id": run_id,
        "coordinate_system": "scene_local_yup",
        "trajectory_sha256": sha256(trajectory_path),
        "point_count": len(trajectory["points"]),
        "extrinsics_verified": True,
        "alignment": {
            "alignment_id": "alignment-fixture-v1",
            "source_kind": "onsite_verified",
            "verified": True,
            "sha256": sha256(alignment_path),
        },
    }
    (run_dir / "replay/trajectory.provenance.json").write_text(
        json.dumps(provenance, allow_nan=False),
        encoding="utf-8",
    )
    operational = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": "completed",
        "started_at": "2026-08-23T04:00:00Z",
        "ended_at": "2026-08-23T04:00:03Z",
        "git_commit": "deadbeef",
        "config_sha256": sha256(config),
        "run_mode": "production",
        "model_version": None,
        "recording": {
            "profile_id": "routine",
            "config_path": "config/recording.yaml",
            "config_sha256": sha256(recording),
            "storage_config_path": "config/mcap_writer_options.yaml",
            "storage_config_sha256": sha256(storage_config),
            "recorder_exit_code": 0,
            "log_path": "recording/rosbag.log",
        },
        "bag": {
            "storage_id": "mcap",
            "path": str(run_dir / "bag" / run_id),
            **inspect_bag(
                run_dir / "bag" / run_id,
                required_topics=["/test"],
            ).manifest_fields(),
        },
        "artifacts": [],
        "exit_reason": "stopped_by_service",
    }
    (run_dir / "manifest.json").write_text(json.dumps(operational), encoding="utf-8")
    return run_dir


def read_checksum_manifest(path: Path) -> dict[str, str]:
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        checksum, relative = line.split(None, 1)
        entries[relative.strip()] = checksum
    return entries


def assert_checksums_match(bundle: Path) -> None:
    for manifest_name in ("manifest-sha256.txt", "tagmanifest-sha256.txt"):
        for relative, expected in read_checksum_manifest(bundle / manifest_name).items():
            assert sha256(bundle / relative) == expected


def add_verified_fixed_route(run_dir: Path) -> None:
    """Attach a production-shaped route snapshot to the run fixture."""
    route_path = run_dir / "config/fixed_route.yaml"
    route_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "route_id": "route-a-v1",
                "source_kind": "onsite_taught",
                "frame_id": "map",
                "verified": True,
                "verified_at": "2026-08-23T03:00:00Z",
                "source_reference": "field-review-001",
                "approved_max_linear_mps": 0.2,
                "poses": [
                    {"x_m": 0.0, "y_m": 0.0, "yaw_rad": 0.0},
                    {"x_m": 1.0, "y_m": 0.0, "yaw_rad": 0.0},
                ],
                "stations": [
                    {
                        "station_id": "station-001",
                        "pose_index": 1,
                        "required": True,
                        "tag_ids": ["tag-001"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixed_route"] = {
        "route_id": "route-a-v1",
        "source_path": "/data/scout_routes/route-a-v1.yaml",
        "path": "config/fixed_route.yaml",
        "sha256": sha256(route_path),
        "verified": True,
    }
    manifest["route_execution"] = {
        "execution_id": "routeexec001",
        "execution_started_ns": 1_787_472_000_000_000_000,
        "state": "completed",
        "reason": "completed",
        "attempts": 1,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def add_pointcloud_artifact(run_dir: Path) -> None:
    """Attach one contract-valid binary PCD chunk to the run fixture."""
    pointcloud_root = run_dir / "artifacts/pointcloud"
    chunks = pointcloud_root / "chunks"
    chunks.mkdir(parents=True)
    pcd_path = chunks / "000000.pcd"
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\n"
        "FIELDS x y z intensity\n"
        "SIZE 4 4 4 4\n"
        "TYPE F F F F\n"
        "COUNT 1 1 1 1\n"
        "WIDTH 1\n"
        "HEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\n"
        "POINTS 1\n"
        "DATA binary\n"
    ).encode("ascii")
    pcd_path.write_bytes(header + struct.pack("<ffff", 1.0, 2.0, 3.0, 4.0))
    fast_lio_path = run_dir / "config/fast_lio.yaml"
    fast_lio_path.write_text(
        "/**:\n  ros__parameters:\n    publish:\n      scan_publish_en: true\n",
        encoding="utf-8",
    )
    export_config_path = run_dir / "config/pointcloud_export.yaml"
    export_config_path.write_text(
        "schema_version: '1.0'\nsource_topic: /cloud_registered\n",
        encoding="utf-8",
    )
    alignment_path = run_dir / "config/scene_alignment.yaml"
    lock = json.loads((CONFIG_DIR / "run-bundle.lock.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "1.0",
        "run_id": "run-20260823-001",
        "coordinate_system": "scene_local_yup",
        "exporter": {
            "package": "inspection_pipeline",
            "version": "0.1.0",
            "git_commit": "deadbeef",
        },
        "source": {
            "bag_path": "bag/run-20260823-001",
            "bag_storage_id": "mcap",
            "topic": "/cloud_registered",
            "type": "sensor_msgs/msg/PointCloud2",
            "fields": ["x", "y", "z", "intensity"],
            "frame_id": "camera_init",
            "message_count": 1,
            "input_point_count": 1,
            "header_stamp_ns": {"first": 100, "last": 100},
            "bag_stamp_ns": {"first": 110, "last": 110},
        },
        "processor": {
            "backend": "pcl::VoxelGrid+PCDWriter",
            "pcl_version": "1.12.1",
            "writer_format": "pcd-0.7-binary-xyzi",
            "selection_policy": "all_registered_scans_by_header_time_window",
            "chunk_duration_ns": 5_000_000_000,
            "voxel_leaf_m": 0.1,
            "limits": {
                "max_input_points_per_message": 500_000,
                "max_input_points_per_chunk": 5_000_000,
                "max_output_points_per_chunk": 1_000_000,
                "max_chunks": 720,
                "max_duration_ns": 3_600_000_000_000,
                "max_abs_coordinate_m": 10_000.0,
            },
            "config": {
                "path": "config/pointcloud_export.yaml",
                "sha256": sha256(export_config_path),
            },
        },
        "map_frame": "map",
        "frame_alias": {
            "parent_frame": "map",
            "child_frame": "camera_init",
            "identity": True,
        },
        "extrinsics_verified": True,
        "system_config_sha256": sha256(run_dir / "config/system.yaml"),
        "fast_lio_config": {
            "source_name": "mid360.yaml",
            "path": "config/fast_lio.yaml",
            "sha256": sha256(fast_lio_path),
        },
        "alignment": {
            "alignment_id": "alignment-fixture-v1",
            "source_kind": "onsite_verified",
            "verified": True,
            "sha256": sha256(alignment_path),
            "transform": {
                "translation_m": [0.0, 0.0, 0.0],
                "rotation_xyzw": [-0.7071067811865475, 0.0, 0.0, 0.7071067811865476],
            },
        },
        "contract_authority": lock["authority"],
        "chunks": [
            {
                "seq": 0,
                "window_index": 0,
                "path": "artifacts/pointcloud/chunks/000000.pcd",
                "header_stamp_ns": {"first": 100, "last": 100},
                "bag_stamp_ns": {"first": 110, "last": 110},
                "message_count": 1,
                "input_point_count": 1,
                "output_point_count": 1,
                "bounds_m": {"min": [1.0, 2.0, 3.0], "max": [1.0, 2.0, 3.0]},
                "sha256": sha256(pcd_path),
            }
        ],
    }
    (pointcloud_root / "manifest.json").write_text(
        json.dumps(manifest, allow_nan=False), encoding="utf-8"
    )


def test_contract_lock_pins_offline_bundle_authority() -> None:
    lock = json.loads((CONFIG_DIR / "run-bundle.lock.json").read_text(encoding="utf-8"))
    assert lock["transport"] == "usb_bagit_directory"
    assert lock["bagit_version"] == "1.0"
    assert lock["checksum_algorithm"] == "sha256"
    assert lock["customer_data"] is False
    assert lock["authority"]["commit"] == "224f71d3a82dd093ad7294c4ebf3d71aef4ce4ab"
    assert lock["authority"]["schema_sha256"] == (
        "033e1ae8dc9b546b5a6967762cf8ab7dadf0d3cf80c84cd50f7c8c324ae08beb"
    )


def test_export_creates_complete_bagit_without_mutating_source(tmp_path: Path) -> None:
    run_dir = create_finalized_run(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()

    bundle = export_run_bundle(
        run_dir,
        usb,
        scene_id="scene-001",
        alignment_id="alignment-fixture-v1",
        name="离线巡检测试",
    )

    assert bundle == usb / "scout-run-run-20260823-001.bag"
    assert (bundle / "bagit.txt").read_text().startswith("BagIt-Version: 1.0\n")
    assert_checksums_match(bundle)
    assert (run_dir / "manifest.json").is_file()
    assert (bundle / "data/bag/run-20260823-001/run-20260823-001_0.mcap").is_file()
    assert (bundle / "data/config/camera_calibration.yaml").is_file()
    assert (bundle / "data/config/scene_alignment.yaml").is_file()
    assert (bundle / "data/replay/trajectory.provenance.json").is_file()
    assert (bundle / "data/recording/rosbag.log").is_file()
    assert bundle.stat().st_mode & 0o555 == 0o555
    assert (bundle / "data/manifest.json").stat().st_mode & 0o444 == 0o444

    manifest = json.loads((bundle / "data/manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_kind"] == "inspection_run"
    assert manifest["scene_id"] == "scene-001"
    assert manifest["coordinate_system"] == "scene_local_yup"
    assert manifest["bag"] == {
        "storage_id": "mcap",
        "path": "bag/run-20260823-001",
    }
    assert manifest["replay"]["trajectory_path"] == "replay/trajectory.json"
    assert not list(usb.glob(".scout-run-*"))


def test_export_rejects_commissioning_run(tmp_path: Path) -> None:
    """A valid debug recording must never become customer delivery evidence."""
    run_dir = create_finalized_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_mode"] = "commissioning"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    usb = tmp_path / "usb"
    usb.mkdir()

    with pytest.raises(BundleExportError, match="only production"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_validates_declares_and_copies_pointcloud_chunks(tmp_path: Path) -> None:
    """Derived PCD bytes must be semantic artifacts as well as BagIt payload."""
    run_dir = create_finalized_run(tmp_path)
    add_pointcloud_artifact(run_dir)
    usb = tmp_path / "usb"
    usb.mkdir()

    bundle = export_run_bundle(
        run_dir,
        usb,
        scene_id="scene-001",
        alignment_id="alignment-fixture-v1",
    )

    semantic = json.loads(
        (bundle / "data/manifest.json").read_text(encoding="utf-8")
    )
    assert semantic["artifacts"] == [
        {
            "path": "artifacts/pointcloud/chunks/000000.pcd",
            "kind": "pointcloud",
            "display": True,
        }
    ]
    assert (bundle / "data/artifacts/pointcloud/manifest.json").is_file()
    assert (bundle / "data/artifacts/pointcloud/chunks/000000.pcd").is_file()
    assert_checksums_match(bundle)


def test_export_rejects_tampered_pointcloud_chunk(tmp_path: Path) -> None:
    """The semantic artifact cannot diverge from its deterministic manifest."""
    run_dir = create_finalized_run(tmp_path)
    add_pointcloud_artifact(run_dir)
    pcd_path = run_dir / "artifacts/pointcloud/chunks/000000.pcd"
    pcd_path.write_bytes(pcd_path.read_bytes() + b"tampered")
    usb = tmp_path / "usb"
    usb.mkdir()

    with pytest.raises(BundleExportError, match="invalid point-cloud artifact"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_refuses_to_overwrite_existing_bundle(tmp_path: Path) -> None:
    run_dir = create_finalized_run(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    export_run_bundle(
        run_dir,
        usb,
        scene_id="scene-001",
        alignment_id="alignment-fixture-v1",
    )

    with pytest.raises(BundleExportError, match="destination already exists"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_validates_and_copies_verified_fixed_route(tmp_path: Path) -> None:
    """Route provenance must survive USB transfer without silent substitution."""
    run_dir = create_finalized_run(tmp_path)
    add_verified_fixed_route(run_dir)
    usb = tmp_path / "usb"
    usb.mkdir()

    bundle = export_run_bundle(
        run_dir,
        usb,
        scene_id="scene-001",
        alignment_id="alignment-fixture-v1",
    )

    assert (bundle / "data/config/fixed_route.yaml").is_file()
    operational = json.loads(
        (bundle / "data/metadata/jetson-manifest.json").read_text(encoding="utf-8")
    )
    assert operational["fixed_route"]["route_id"] == "route-a-v1"


def test_export_rejects_fixed_route_hash_or_verification_mismatch(tmp_path: Path) -> None:
    """An edited or commissioning-only route cannot become delivery evidence."""
    run_dir = create_finalized_run(tmp_path)
    add_verified_fixed_route(run_dir)
    usb = tmp_path / "usb"
    usb.mkdir()
    route_path = run_dir / "config/fixed_route.yaml"
    route = yaml.safe_load(route_path.read_text(encoding="utf-8"))
    route["verified"] = False
    route["verified_at"] = None
    route_path.write_text(yaml.safe_dump(route, sort_keys=False), encoding="utf-8")

    with pytest.raises(BundleExportError, match="SHA-256 mismatch"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )

    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixed_route"]["sha256"] = sha256(route_path)
    manifest["fixed_route"]["verified"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BundleExportError, match="not approved for export"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_rejects_completed_status_for_paused_route(tmp_path: Path) -> None:
    """A paused route cannot be relabeled as a completed inspection bundle."""
    run_dir = create_finalized_run(tmp_path)
    add_verified_fixed_route(run_dir)
    usb = tmp_path / "usb"
    usb.mkdir()
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["route_execution"] = {
        "execution_id": "routeexec001",
        "execution_started_ns": 1_787_472_000_000_000_000,
        "state": "paused",
        "reason": "safety_stop:obstacle_stop",
        "attempts": 1,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BundleExportError, match="contradicts"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_rejects_completed_route_that_was_never_started(tmp_path: Path) -> None:
    """Configured route provenance alone is not evidence of route execution."""
    run_dir = create_finalized_run(tmp_path)
    add_verified_fixed_route(run_dir)
    usb = tmp_path / "usb"
    usb.mkdir()
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("route_execution")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BundleExportError, match="contradicts"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_rejects_relative_route_source_or_predated_execution(
    tmp_path: Path,
) -> None:
    """Externally edited route provenance must still obey the runtime contract."""
    run_dir = create_finalized_run(tmp_path)
    add_verified_fixed_route(run_dir)
    usb = tmp_path / "usb"
    usb.mkdir()
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixed_route"]["source_path"] = "relative/route.yaml"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BundleExportError, match="source_path"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )

    manifest["fixed_route"]["source_path"] = "/data/scout_routes/route-a-v1.yaml"
    manifest["route_execution"]["execution_started_ns"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BundleExportError, match="predates"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_rejects_nonterminal_run_and_config_mismatch(tmp_path: Path) -> None:
    run_dir = create_finalized_run(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "running"
    manifest["ended_at"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BundleExportError, match="not finalized"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )

    manifest["status"] = "completed"
    manifest["ended_at"] = "2026-08-23T04:00:03Z"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "config/system.yaml").write_text("changed: true\n", encoding="utf-8")
    with pytest.raises(BundleExportError, match="config_sha256"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_rejects_links_from_run_payload(tmp_path: Path) -> None:
    run_dir = create_finalized_run(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    (run_dir / "artifacts/unsafe-link").symlink_to(run_dir / "manifest.json")

    with pytest.raises(BundleExportError, match="symbolic links are forbidden"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_rejects_synthetic_scene_alignment(tmp_path: Path) -> None:
    """A contract fixture must never be labeled as on-site inspection evidence."""
    run_dir = create_finalized_run(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    provenance_path = run_dir / "replay/trajectory.provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["alignment"]["source_kind"] = "synthetic_contract_fixture"
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    with pytest.raises(BundleExportError, match="synthetic scene alignment"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_rejects_invalid_trajectory_numbers(tmp_path: Path) -> None:
    """Invalid positions cannot pass Python JSON parsing into the contract."""
    run_dir = create_finalized_run(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    trajectory_path = run_dir / "replay/trajectory.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["points"][0]["position"]["x"] = float("nan")
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")

    with pytest.raises(BundleExportError, match="invalid replay trajectory"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_export_rejects_non_mcap_manifest_or_metadata(tmp_path: Path) -> None:
    """Both the run declaration and actual rosbag metadata must prove MCAP."""
    run_dir = create_finalized_run(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bag"]["storage_id"] = "sqlite3"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BundleExportError, match="bag.storage_id"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )

    manifest["bag"]["storage_id"] = "mcap"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    metadata_path = run_dir / "bag/run-20260823-001/metadata.yaml"
    metadata_path.write_text(
        "rosbag2_bagfile_information:\n  storage_identifier: sqlite3\n",
        encoding="utf-8",
    )
    with pytest.raises(BundleExportError, match="storage_identifier must be mcap"):
        export_run_bundle(
            run_dir,
            usb,
            scene_id="scene-001",
            alignment_id="alignment-fixture-v1",
        )


def test_run_validator_scans_mcap_and_hash_inventory(tmp_path: Path) -> None:
    run_dir = create_finalized_run(tmp_path)

    report = validate_run_directory(run_dir)

    assert report["valid"] is True
    assert report["checks"]["mcap_doctor"] is True
    assert report["checks"]["mcap_full_scan"] is True
    assert report["bag"]["message_count"] == 1
    assert report["bag"]["scanned_message_count"] == 1
    assert report["bag"]["duration_seconds"] == 0
    assert report["bag"]["topic_counts"] == {"/test": 1}
    assert report["bag"]["topic_average_hz"] == {"/test": None}
    assert any(item["path"].endswith(".mcap") for item in report["files"])


def test_recording_only_validator_accepts_commissioning_run(tmp_path: Path) -> None:
    run_dir = create_finalized_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_mode"] = "commissioning"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = validate_run_directory(run_dir, recording_only=True)

    assert report["valid"] is True
    assert report["run_mode"] == "commissioning"
    assert report["checks"]["trajectory"] is False


def test_bagit_readback_rejects_changed_usb_payload(tmp_path: Path) -> None:
    run_dir = create_finalized_run(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    bundle = export_run_bundle(
        run_dir,
        usb,
        scene_id="scene-001",
        alignment_id="alignment-fixture-v1",
    )
    trajectory = bundle / "data/replay/trajectory.json"
    trajectory.write_bytes(trajectory.read_bytes() + b"tampered")

    with pytest.raises(BundleVerificationError, match="checksum mismatch"):
        verify_bagit_bundle(bundle)


def test_usb_finalize_verifies_flushes_and_unmounts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = create_finalized_run(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    export_run_bundle(
        run_dir,
        usb,
        scene_id="scene-001",
        alignment_id="alignment-fixture-v1",
    )
    mount = UsbMount(
        target=usb.resolve(),
        source="/dev/sdz1",
        filesystem="exfat",
        uuid="fixture-uuid",
    )
    monkeypatch.setattr(usb_finalize_module, "inspect_usb_mount", lambda _path: mount)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return_code = 1 if command[0] == "findmnt" else 0
        return subprocess.CompletedProcess(command, return_code, stdout="", stderr="")

    monkeypatch.setattr(usb_finalize_module.subprocess, "run", fake_run)

    report = finalize_usb(usb, unmount=True)

    assert report["valid"] is True
    assert report["synced"] is True
    assert report["unmounted"] is True
    assert report["uuid"] == "fixture-uuid"
    assert ["sync", "-f", str(usb.resolve())] in calls
    assert ["udisksctl", "unmount", "--block-device", "/dev/sdz1"] in calls
