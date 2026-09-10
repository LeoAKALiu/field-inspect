"""Tests for immutable per-run configuration snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from inspection_pipeline.config_snapshot import ConfigSnapshotError, snapshot_run_config


def write_system_config(
    path: Path,
    calibration_url: str,
    target_url: str | None = None,
    profile_url: str | None = None,
) -> None:
    """Write the camera portion needed by the snapshot helper."""
    if target_url is None:
        target_url = calibration_url
    if profile_url is None:
        profile_url = calibration_url
    path.write_text(
        "hik_camera_node:\n"
        "  ros__parameters:\n"
        "    camera_name: hik_mv_cs050_10gc\n"
        "    camera_model: MV-CS050-10GC\n"
        "    camera_serial: DA1234567\n"
        "    lens_id: lens-01-focus-locked-v1\n"
        "    sensor_width: 2448\n"
        "    sensor_height: 2048\n"
        "    pixel_format: BGR8\n"
        "    width: 1280\n"
        "    height: 1024\n"
        "    offset_x: 584\n"
        "    offset_y: 512\n"
        f'    calibration_url: "{calibration_url}"\n'
        "inspection_manager:\n"
        "  ros__parameters:\n"
        f'    calibration_target_url: "{target_url}"\n'
        f'    calibration_profile_url: "{profile_url}"\n'
        "route_orchestrator:\n"
        "  ros__parameters:\n"
        '    fixed_route_path: ""\n',
        encoding="utf-8",
    )


def write_verified_target(path: Path) -> None:
    path.write_text(
        "schema_version: '1.0'\n"
        "target_id: a2-checkerboard-9x6-50mm-v1\n"
        "pattern: checkerboard\n"
        "columns: 9\n"
        "rows: 6\n"
        "square_size_m: 0.05\n"
        "marker_size_m: null\n"
        "aruco_dictionary: null\n"
        "dimensions_verified: true\n"
        "verified_at: '2026-08-24T08:00:00Z'\n"
        "measurement_method: steel_ruler_three_locations\n",
        encoding="utf-8",
    )


def write_camera_calibration(path: Path) -> None:
    path.write_text(
        "image_width: 1280\n"
        "image_height: 1024\n"
        "camera_name: hik_mv_cs050_10gc\n"
        "camera_matrix:\n"
        "  rows: 3\n"
        "  cols: 3\n"
        "  data: [800.0, 0.0, 640.0, 0.0, 801.0, 512.0, 0.0, 0.0, 1.0]\n"
        "distortion_model: plumb_bob\n"
        "distortion_coefficients:\n"
        "  rows: 1\n"
        "  cols: 5\n"
        "  data: [0.01, -0.02, 0.0, 0.0, 0.0]\n"
        "rectification_matrix:\n"
        "  rows: 3\n"
        "  cols: 3\n"
        "  data: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]\n"
        "projection_matrix:\n"
        "  rows: 3\n"
        "  cols: 4\n"
        "  data: [800.0, 0.0, 640.0, 0.0, 0.0, 801.0, 512.0, 0.0, 0.0, 0.0, 1.0, 0.0]\n",
        encoding="utf-8",
    )


def write_profile(path: Path, calibration: Path, target: Path) -> None:
    identity = {
        "camera": {
            "camera_name": "hik_mv_cs050_10gc",
            "camera_model": "MV-CS050-10GC",
            "camera_serial": "DA1234567",
            "lens_id": "lens-01-focus-locked-v1",
            "sensor_width": 2448,
            "sensor_height": 2048,
        },
        "stream": {
            "pixel_format": "BGR8",
            "width": 1280,
            "height": 1024,
            "offset_x": 584,
            "offset_y": 512,
        },
    }
    identity_hash = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    profile = {
        "schema_version": "1.0",
        "profile_id": "hik-cs050-roi-v1",
        "calibrated_at": "2026-08-28T08:00:00Z",
        "identity": identity,
        "identity_sha256": identity_hash,
        "target": {
            "target_id": "a2-checkerboard-9x6-50mm-v1",
            "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        },
        "calibration": {
            "camera_info_sha256": hashlib.sha256(calibration.read_bytes()).hexdigest(),
            "source_archive_sha256": "f" * 64,
            "source_archive_name": "calibrationdata.tar.gz",
            "upstream_package": "camera_calibration 3.0.6",
        },
        "quality": {"sample_count": 31},
    }
    path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def test_snapshot_copies_file_calibration_and_records_hash(tmp_path: Path) -> None:
    """A configured local calibration must travel with every run."""
    calibration = tmp_path / "camera.yaml"
    write_camera_calibration(calibration)
    target = tmp_path / "target.yaml"
    write_verified_target(target)
    profile = tmp_path / "camera.profile.yaml"
    write_profile(profile, calibration, target)
    system = tmp_path / "system.yaml"
    write_system_config(
        system,
        calibration.as_uri(),
        target.as_uri(),
        profile.as_uri(),
    )

    metadata = snapshot_run_config(system, tmp_path / "run_config")

    snapshot = tmp_path / "run_config/camera_calibration.yaml"
    assert snapshot.read_bytes() == calibration.read_bytes()
    assert metadata.camera_calibration == {
        "source_url": calibration.as_uri(),
        "path": "config/camera_calibration.yaml",
        "sha256": hashlib.sha256(calibration.read_bytes()).hexdigest(),
        "target_id": "a2-checkerboard-9x6-50mm-v1",
        "target_pattern": "checkerboard",
        "target_verified_at": "2026-08-24T08:00:00Z",
        "target_source_url": target.as_uri(),
        "target_path": "config/camera_calibration_target.yaml",
        "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "profile_id": "hik-cs050-roi-v1",
        "profile_source_url": profile.as_uri(),
        "profile_path": "config/camera_calibration_profile.yaml",
        "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        "identity_sha256": yaml.safe_load(profile.read_text(encoding="utf-8"))[
            "identity_sha256"
        ],
        "camera_model": "MV-CS050-10GC",
        "camera_serial": "DA1234567",
        "lens_id": "lens-01-focus-locked-v1",
    }
    assert metadata.fixed_route is None
    assert metadata.fast_lio_config is None
    assert metadata.extrinsics_verified is False
    assert (
        tmp_path / "run_config/camera_calibration_target.yaml"
    ).read_bytes() == target.read_bytes()
    assert (
        tmp_path / "run_config/camera_calibration_profile.yaml"
    ).read_bytes() == profile.read_bytes()


def test_snapshot_allows_explicitly_uncalibrated_config(tmp_path: Path) -> None:
    """Raw-image commissioning remains possible when calibration is not configured."""
    system = tmp_path / "system.yaml"
    write_system_config(system, "")

    metadata = snapshot_run_config(system, tmp_path / "run_config")

    assert metadata.camera_calibration is None
    assert metadata.fixed_route is None
    assert metadata.fast_lio_config is None
    assert metadata.extrinsics_verified is False
    assert (tmp_path / "run_config/system.yaml").is_file()
    assert not (tmp_path / "run_config/camera_calibration.yaml").exists()


def test_snapshot_rejects_missing_configured_calibration(tmp_path: Path) -> None:
    """A run must not start with an untraceable configured calibration."""
    system = tmp_path / "system.yaml"
    write_system_config(system, (tmp_path / "missing.yaml").as_uri())

    with pytest.raises(ConfigSnapshotError, match="does not exist"):
        snapshot_run_config(system, tmp_path / "run_config")


def test_snapshot_rejects_package_path_traversal(tmp_path: Path) -> None:
    """A package URL must not read arbitrary files outside its package share."""
    package_share = tmp_path / "share/test_camera"
    package_share.mkdir(parents=True)
    outside = tmp_path / "share/outside.yaml"
    outside.write_text("camera_name: outside\n", encoding="utf-8")
    system = tmp_path / "system.yaml"
    write_system_config(system, "package://test_camera/../outside.yaml")

    with pytest.raises(ConfigSnapshotError, match="escapes"):
        snapshot_run_config(
            system,
            tmp_path / "run_config",
            package_resolver=lambda _name: str(package_share),
        )


def test_snapshot_copies_configured_fixed_route(tmp_path: Path) -> None:
    """A configured reviewed path must be immutable and traceable per run."""
    route = tmp_path / "route.yaml"
    route.write_text(
        "schema_version: '1.0'\n"
        "route_id: route-test-v1\n"
        "source_kind: synthetic_contract_fixture\n"
        "frame_id: map\n"
        "verified: false\n"
        "verified_at: null\n"
        "source_reference: ''\n"
        "approved_max_linear_mps: 0.2\n"
        "poses:\n"
        "  - {x_m: 0.0, y_m: 0.0, yaw_rad: 0.0}\n"
        "  - {x_m: 1.0, y_m: 0.0, yaw_rad: 0.0}\n"
        "stations:\n"
        "  - station_id: station-001\n"
        "    pose_index: 1\n"
        "    required: true\n"
        "    tag_ids: []\n",
        encoding="utf-8",
    )
    system = tmp_path / "system.yaml"
    write_system_config(system, "")
    content = system.read_text(encoding="utf-8").replace(
        '    fixed_route_path: ""',
        f'    fixed_route_path: "{route}"',
    )
    system.write_text(content, encoding="utf-8")

    metadata = snapshot_run_config(system, tmp_path / "run_config")

    snapshot = tmp_path / "run_config/fixed_route.yaml"
    assert snapshot.read_bytes() == route.read_bytes()
    assert metadata.fixed_route == {
        "route_id": "route-test-v1",
        "source_path": str(route),
        "path": "config/fixed_route.yaml",
        "sha256": hashlib.sha256(route.read_bytes()).hexdigest(),
        "verified": False,
    }
    assert metadata.fast_lio_config is None


def test_snapshot_rejects_relative_fixed_route_path(tmp_path: Path) -> None:
    """A run must not resolve its route against a process-dependent directory."""
    system = tmp_path / "system.yaml"
    write_system_config(system, "")
    content = system.read_text(encoding="utf-8").replace(
        '    fixed_route_path: ""',
        '    fixed_route_path: "route.yaml"',
    )
    system.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigSnapshotError, match="must be absolute"):
        snapshot_run_config(system, tmp_path / "run_config")


def test_snapshot_copies_fast_lio_config_with_hash(tmp_path: Path) -> None:
    """Offline point-cloud export must use the exact mapping config from the run."""
    fast_lio_share = tmp_path / "share/fast_lio"
    (fast_lio_share / "config").mkdir(parents=True)
    source = tmp_path / "upstream/fast_lio/config/mid360.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("/**:\n  ros__parameters:\n    filter_size_map: 0.5\n", encoding="utf-8")
    (fast_lio_share / "config/mid360.yaml").symlink_to(source)
    system = tmp_path / "system.yaml"
    write_system_config(system, "")
    system.write_text(
        system.read_text(encoding="utf-8")
        + "scout_bringup:\n"
        + "  ros__parameters:\n"
        + '    fast_lio_config_file: "mid360.yaml"\n',
        encoding="utf-8",
    )

    metadata = snapshot_run_config(
        system,
        tmp_path / "run_config",
        package_resolver=lambda name: str(fast_lio_share) if name == "fast_lio" else "",
    )

    snapshot = tmp_path / "run_config/fast_lio.yaml"
    assert snapshot.read_bytes() == source.read_bytes()
    assert metadata.fast_lio_config == {
        "source_package": "fast_lio",
        "source_name": "mid360.yaml",
        "path": "config/fast_lio.yaml",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
