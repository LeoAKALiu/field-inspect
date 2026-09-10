"""Tests for the measured-target camera calibration workflow."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from inspection_pipeline.camera_calibration import (
    CameraCalibrationError,
    build_calibration_command,
    import_calibration_archive,
    load_calibration_profile,
    load_calibration_target,
    read_calibration_archive,
    validate_camera_calibration,
)


CAMERA = {
    "image_topic": "/camera/image_raw",
    "camera_info_topic": "/camera/camera_info",
    "camera_name": "hik_mv_cs050_10gc",
    "camera_model": "MV-CS050-10GC",
    "camera_serial": "DA1234567",
    "lens_id": "lens-01-focus-locked-v1",
    "sensor_width": 2448,
    "sensor_height": 2048,
    "pixel_format": "BGR8",
    "width": 1280,
    "height": 1024,
    "offset_x": 584,
    "offset_y": 512,
}


def write_target(
    path: Path,
    *,
    pattern: str = "checkerboard",
    verified: bool = True,
) -> None:
    marker_size = "0.0375" if pattern == "charuco" else "null"
    dictionary = "4x4_250" if pattern == "charuco" else "null"
    verified_at = "'2026-08-24T08:00:00Z'" if verified else "null"
    method = "steel_ruler_three_locations" if verified else "null"
    path.write_text(
        "schema_version: '1.0'\n"
        f"target_id: target-{pattern}-v1\n"
        f"pattern: {pattern}\n"
        "columns: 9\n"
        "rows: 6\n"
        "square_size_m: 0.05\n"
        f"marker_size_m: {marker_size}\n"
        f"aruco_dictionary: {dictionary}\n"
        f"dimensions_verified: {str(verified).lower()}\n"
        f"verified_at: {verified_at}\n"
        f"measurement_method: {method}\n",
        encoding="utf-8",
    )


def write_calibration(path: Path) -> None:
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


def write_calibration_archive(
    path: Path,
    calibration: Path,
    *,
    extra_member: str | None = None,
) -> None:
    members = {
        "ost.yaml": calibration.read_bytes(),
        "left-0000.png": b"fake-png-0",
        "left-0001.png": b"fake-png-1",
    }
    if extra_member is not None:
        members[extra_member] = b"unexpected"
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_checkerboard_command_uses_ros2_camera_calibration_cli(tmp_path: Path) -> None:
    target_path = tmp_path / "target.yaml"
    write_target(target_path)

    command = build_calibration_command(load_calibration_target(target_path), CAMERA)

    assert command[:8] == [
        "ros2",
        "run",
        "camera_calibration",
        "cameracalibrator",
        "--pattern",
        "chessboard",
        "--size",
        "9x6",
    ]
    assert command[-5:] == [
        "--ros-args",
        "--remap",
        "image:=/camera/image_raw",
        "--remap",
        "camera:=/camera",
    ]


def test_charuco_command_preserves_marker_contract(tmp_path: Path) -> None:
    target_path = tmp_path / "target.yaml"
    write_target(target_path, pattern="charuco")

    command = build_calibration_command(load_calibration_target(target_path), CAMERA)

    assert command[command.index("--pattern") + 1] == "charuco"
    assert command[command.index("--charuco_marker_size") + 1] == "0.0375"
    assert command[command.index("--aruco_dict") + 1] == "4x4_250"


def test_unmeasured_print_cannot_start_real_calibration(tmp_path: Path) -> None:
    target_path = tmp_path / "target.yaml"
    write_target(target_path, verified=False)
    target = load_calibration_target(target_path)

    with pytest.raises(CameraCalibrationError, match="dimensions are unverified"):
        build_calibration_command(target, CAMERA)

    assert build_calibration_command(
        target,
        CAMERA,
        require_verified_dimensions=False,
    )


def test_camera_info_yaml_matches_deployed_identity(tmp_path: Path) -> None:
    calibration = tmp_path / "camera.yaml"
    write_calibration(calibration)

    report = validate_camera_calibration(calibration, CAMERA)

    assert report["valid"] is True
    assert report["camera_name"] == CAMERA["camera_name"]
    assert len(report["sha256"]) == 64


def test_camera_info_yaml_rejects_wrong_resolution(tmp_path: Path) -> None:
    calibration = tmp_path / "camera.yaml"
    write_calibration(calibration)
    content = calibration.read_text(encoding="utf-8").replace(
        "image_width: 1280",
        "image_width: 640",
    )
    calibration.write_text(content, encoding="utf-8")

    with pytest.raises(CameraCalibrationError, match="do not match"):
        validate_camera_calibration(calibration, CAMERA)


def test_import_archive_binds_camera_target_and_source(tmp_path: Path) -> None:
    calibration = tmp_path / "ost.yaml"
    write_calibration(calibration)
    archive = tmp_path / "calibrationdata.tar.gz"
    write_calibration_archive(archive, calibration)
    target = tmp_path / "target.yaml"
    write_target(target)
    output = tmp_path / "installed/camera.yaml"
    profile = tmp_path / "installed/camera.profile.yaml"
    preserved_archive = tmp_path / "installed/camera.source.tar.gz"

    report = import_calibration_archive(
        archive,
        target,
        output,
        profile,
        preserved_archive,
        camera=CAMERA,
        profile_id="hik-cs050-roi-v1",
        calibrated_at="2026-08-28T08:00:00Z",
    )

    assert report["valid"] is True
    assert report["sample_count"] == 2
    assert output.read_bytes() == calibration.read_bytes()
    assert preserved_archive.read_bytes() == archive.read_bytes()
    loaded = load_calibration_profile(
        profile,
        camera=CAMERA,
        calibration_path=output,
        target_path=target,
    )
    assert loaded["profile_id"] == "hik-cs050-roi-v1"
    assert loaded["identity"]["camera"]["camera_serial"] == "DA1234567"
    assert loaded["quality"] == {"sample_count": 2}


def test_profile_rejects_changed_camera_identity(tmp_path: Path) -> None:
    calibration = tmp_path / "ost.yaml"
    write_calibration(calibration)
    archive = tmp_path / "calibrationdata.tar.gz"
    write_calibration_archive(archive, calibration)
    target = tmp_path / "target.yaml"
    write_target(target)
    output = tmp_path / "camera.yaml"
    profile = tmp_path / "camera.profile.yaml"
    preserved_archive = tmp_path / "camera.source.tar.gz"
    import_calibration_archive(
        archive,
        target,
        output,
        profile,
        preserved_archive,
        camera=CAMERA,
        profile_id="hik-cs050-roi-v1",
    )
    changed_camera = {**CAMERA, "camera_serial": "DIFFERENT"}

    with pytest.raises(CameraCalibrationError, match="does not match system config"):
        load_calibration_profile(
            profile,
            camera=changed_camera,
            calibration_path=output,
            target_path=target,
        )


def test_archive_reader_rejects_path_traversal(tmp_path: Path) -> None:
    calibration = tmp_path / "ost.yaml"
    write_calibration(calibration)
    archive = tmp_path / "calibrationdata.tar.gz"
    write_calibration_archive(archive, calibration, extra_member="../escape.txt")

    with pytest.raises(CameraCalibrationError, match="normalized and relative"):
        read_calibration_archive(archive)


def test_import_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    calibration = tmp_path / "ost.yaml"
    write_calibration(calibration)
    archive = tmp_path / "calibrationdata.tar.gz"
    write_calibration_archive(archive, calibration)
    target = tmp_path / "target.yaml"
    write_target(target)
    output = tmp_path / "camera.yaml"
    output.write_text("keep me", encoding="utf-8")

    with pytest.raises(CameraCalibrationError, match="refusing to overwrite"):
        import_calibration_archive(
            archive,
            target,
            output,
            tmp_path / "camera.profile.yaml",
            tmp_path / "camera.source.tar.gz",
            camera=CAMERA,
            profile_id="hik-cs050-roi-v1",
        )

    assert output.read_text(encoding="utf-8") == "keep me"
