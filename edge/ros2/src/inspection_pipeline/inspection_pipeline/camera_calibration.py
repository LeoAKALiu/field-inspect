"""Versioned camera-target commands and CameraInfo YAML validation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import yaml
from ament_index_python.packages import get_package_share_directory


class CameraCalibrationError(ValueError):
    """Camera calibration inputs are unsafe or internally inconsistent."""


TARGET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
CALIBRATION_IMAGE_PATTERN = re.compile(r"^left-[0-9]{4}\.png$")
MAX_ARCHIVE_MEMBERS = 500
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_CAMERA_INFO_BYTES = 1024 * 1024
SUPPORTED_PATTERNS = frozenset({"checkerboard", "charuco"})
SUPPORTED_ARUCO_DICTIONARIES = frozenset(
    {"aruco_orig", "4x4_250", "5x5_250", "6x6_250", "7x7_250"}
)
TARGET_KEYS = frozenset(
    {
        "schema_version",
        "target_id",
        "pattern",
        "columns",
        "rows",
        "square_size_m",
        "marker_size_m",
        "aruco_dictionary",
        "dimensions_verified",
        "verified_at",
        "measurement_method",
    }
)
PROFILE_KEYS = frozenset(
    {
        "schema_version",
        "profile_id",
        "calibrated_at",
        "identity",
        "identity_sha256",
        "target",
        "calibration",
        "quality",
    }
)
IDENTITY_KEYS = frozenset({"camera", "stream"})
CAMERA_IDENTITY_KEYS = frozenset(
    {
        "camera_name",
        "camera_model",
        "camera_serial",
        "lens_id",
        "sensor_width",
        "sensor_height",
    }
)
STREAM_IDENTITY_KEYS = frozenset(
    {"pixel_format", "width", "height", "offset_x", "offset_y"}
)
TARGET_REFERENCE_KEYS = frozenset({"target_id", "target_sha256"})
CALIBRATION_REFERENCE_KEYS = frozenset(
    {
        "camera_info_sha256",
        "source_archive_sha256",
        "source_archive_name",
        "upstream_package",
    }
)
QUALITY_KEYS = frozenset({"sample_count"})


@dataclass(frozen=True)
class CalibrationTarget:
    """Physical target contract consumed by ROS camera_calibration."""

    target_id: str
    pattern: str
    columns: int
    rows: int
    square_size_m: float
    marker_size_m: Optional[float]
    aruco_dictionary: Optional[str]
    dimensions_verified: bool
    verified_at: Optional[str]
    measurement_method: Optional[str]


@dataclass(frozen=True)
class CalibrationArchive:
    """Trusted subset of one upstream camera_calibration save archive."""

    camera_info: bytes
    image_names: tuple[str, ...]


def sha256_file(path: Path) -> str:
    """Hash one regular calibration input."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positive_finite(value: Any, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise CameraCalibrationError(f"{field} must be a positive finite number")
    return float(value)


def _utc_timestamp(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CameraCalibrationError(f"{field} must be null or an ISO 8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CameraCalibrationError(f"{field} must be an ISO 8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise CameraCalibrationError(f"{field} must use an explicit UTC offset")
    return value


def load_calibration_target(path: Path | str) -> CalibrationTarget:
    """Load and strictly validate one measured checkerboard/ChArUco target."""
    source = Path(path).resolve()
    if not source.is_file():
        raise CameraCalibrationError(f"target must be a regular file: {source}")
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CameraCalibrationError(f"cannot load calibration target: {exc}") from exc
    if not isinstance(document, dict) or set(document) != TARGET_KEYS:
        raise CameraCalibrationError(
            "calibration target must contain exactly: " + ", ".join(sorted(TARGET_KEYS))
        )
    if document.get("schema_version") != "1.0":
        raise CameraCalibrationError("calibration target schema_version must be 1.0")
    target_id = document.get("target_id")
    if not isinstance(target_id, str) or TARGET_ID_PATTERN.fullmatch(target_id) is None:
        raise CameraCalibrationError("target_id is not a valid versioned identifier")
    pattern = document.get("pattern")
    if pattern not in SUPPORTED_PATTERNS:
        raise CameraCalibrationError(
            f"pattern must be one of {sorted(SUPPORTED_PATTERNS)}"
        )
    columns = document.get("columns")
    rows = document.get("rows")
    if not isinstance(columns, int) or isinstance(columns, bool) or columns < 4:
        raise CameraCalibrationError("columns must be an integer >= 4")
    if not isinstance(rows, int) or isinstance(rows, bool) or rows < 3:
        raise CameraCalibrationError("rows must be an integer >= 3")
    square_size_m = _positive_finite(document.get("square_size_m"), "square_size_m")
    marker_size_value = document.get("marker_size_m")
    dictionary_value = document.get("aruco_dictionary")
    if pattern == "checkerboard":
        if marker_size_value is not None or dictionary_value is not None:
            raise CameraCalibrationError(
                "checkerboard marker_size_m and aruco_dictionary must be null"
            )
        marker_size_m = None
        aruco_dictionary = None
    else:
        marker_size_m = _positive_finite(marker_size_value, "marker_size_m")
        if marker_size_m >= square_size_m:
            raise CameraCalibrationError("marker_size_m must be less than square_size_m")
        if dictionary_value not in SUPPORTED_ARUCO_DICTIONARIES:
            raise CameraCalibrationError(
                "aruco_dictionary must be one of "
                + ", ".join(sorted(SUPPORTED_ARUCO_DICTIONARIES))
            )
        aruco_dictionary = str(dictionary_value)
    dimensions_verified = document.get("dimensions_verified")
    if not isinstance(dimensions_verified, bool):
        raise CameraCalibrationError("dimensions_verified must be a boolean")
    verified_at = _utc_timestamp(document.get("verified_at"), "verified_at")
    measurement_method = document.get("measurement_method")
    if measurement_method is not None and (
        not isinstance(measurement_method, str) or not measurement_method.strip()
    ):
        raise CameraCalibrationError("measurement_method must be null or non-empty text")
    if dimensions_verified and (verified_at is None or measurement_method is None):
        raise CameraCalibrationError(
            "verified target dimensions require verified_at and measurement_method"
        )
    if not dimensions_verified and (verified_at is not None or measurement_method is not None):
        raise CameraCalibrationError(
            "unverified target dimensions cannot claim verification evidence"
        )
    return CalibrationTarget(
        target_id=target_id,
        pattern=pattern,
        columns=columns,
        rows=rows,
        square_size_m=square_size_m,
        marker_size_m=marker_size_m,
        aruco_dictionary=aruco_dictionary,
        dimensions_verified=dimensions_verified,
        verified_at=verified_at,
        measurement_method=measurement_method,
    )


def _positive_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CameraCalibrationError(f"{field} must be a positive integer")
    return value


def _nonnegative_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CameraCalibrationError(f"{field} must be a non-negative integer")
    return value


def camera_acquisition_identity(camera: dict[str, Any]) -> dict[str, Any]:
    """Return the physical camera and ROI identity that an intrinsic belongs to."""
    strings: dict[str, str] = {}
    for key in ("camera_name", "camera_model", "camera_serial", "lens_id"):
        value = camera.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CameraCalibrationError(
                f"camera {key} must be configured before calibration import"
            )
        strings[key] = value

    sensor_width = _positive_integer(camera.get("sensor_width"), "sensor_width")
    sensor_height = _positive_integer(camera.get("sensor_height"), "sensor_height")
    width = _positive_integer(camera.get("width"), "width")
    height = _positive_integer(camera.get("height"), "height")
    offset_x = _nonnegative_integer(camera.get("offset_x"), "offset_x")
    offset_y = _nonnegative_integer(camera.get("offset_y"), "offset_y")
    if offset_x + width > sensor_width or offset_y + height > sensor_height:
        raise CameraCalibrationError("camera ROI exceeds the configured sensor bounds")
    pixel_format = camera.get("pixel_format")
    if not isinstance(pixel_format, str) or not pixel_format:
        raise CameraCalibrationError("camera pixel_format must be configured")

    return {
        "camera": {
            **strings,
            "sensor_width": sensor_width,
            "sensor_height": sensor_height,
        },
        "stream": {
            "pixel_format": pixel_format,
            "width": width,
            "height": height,
            "offset_x": offset_x,
            "offset_y": offset_y,
        },
    }


def acquisition_identity_sha256(identity: dict[str, Any]) -> str:
    """Hash a canonical camera acquisition identity."""
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_calibration_archive(path: Path | str) -> CalibrationArchive:
    """Read only the safe, bounded members produced by ROS camera_calibration."""
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise CameraCalibrationError(
            f"calibration archive must be a regular file: {source}"
        )
    try:
        with tarfile.open(source, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise CameraCalibrationError("calibration archive member count is invalid")
            names = [member.name for member in members]
            if len(set(names)) != len(names):
                raise CameraCalibrationError("calibration archive contains duplicate paths")
            total_size = 0
            for member in members:
                relative = PurePosixPath(member.name)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or member.name != relative.as_posix()
                ):
                    raise CameraCalibrationError(
                        "calibration archive paths must be normalized and relative"
                    )
                if not member.isfile():
                    raise CameraCalibrationError(
                        "calibration archive may contain regular files only"
                    )
                if member.size < 0:
                    raise CameraCalibrationError(
                        "calibration archive contains an invalid member size"
                    )
                total_size += member.size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise CameraCalibrationError("calibration archive is too large")
            if names.count("ost.yaml") != 1:
                raise CameraCalibrationError(
                    "calibration archive must contain exactly one ost.yaml"
                )
            camera_member = archive.getmember("ost.yaml")
            if camera_member.size <= 0 or camera_member.size > MAX_CAMERA_INFO_BYTES:
                raise CameraCalibrationError("ost.yaml size is invalid")
            stream = archive.extractfile(camera_member)
            if stream is None:
                raise CameraCalibrationError("cannot read ost.yaml from archive")
            camera_info = stream.read(MAX_CAMERA_INFO_BYTES + 1)
            if len(camera_info) != camera_member.size:
                raise CameraCalibrationError("ost.yaml content is truncated")
            image_names = tuple(
                sorted(name for name in names if CALIBRATION_IMAGE_PATTERN.fullmatch(name))
            )
            if not image_names:
                raise CameraCalibrationError(
                    "calibration archive contains no saved calibration images"
                )
    except (OSError, tarfile.TarError) as exc:
        raise CameraCalibrationError(f"cannot read calibration archive: {exc}") from exc
    return CalibrationArchive(camera_info=camera_info, image_names=image_names)


def _strict_mapping(
    value: Any,
    expected_keys: frozenset[str],
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise CameraCalibrationError(
            f"{field} must contain exactly: " + ", ".join(sorted(expected_keys))
        )
    return value


def load_calibration_profile(
    profile_path: Path | str,
    *,
    camera: dict[str, Any],
    calibration_path: Path | str,
    target_path: Path | str,
) -> dict[str, Any]:
    """Validate the sidecar that binds a CameraInfo YAML to hardware and ROI."""
    source = Path(profile_path)
    if not source.is_file() or source.is_symlink():
        raise CameraCalibrationError(f"calibration profile must be a regular file: {source}")
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CameraCalibrationError(f"cannot load calibration profile: {exc}") from exc
    profile = _strict_mapping(document, PROFILE_KEYS, "calibration profile")
    if profile.get("schema_version") != "1.0":
        raise CameraCalibrationError("calibration profile schema_version must be 1.0")
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or TARGET_ID_PATTERN.fullmatch(profile_id) is None:
        raise CameraCalibrationError("calibration profile_id is invalid")
    if _utc_timestamp(profile.get("calibrated_at"), "calibrated_at") is None:
        raise CameraCalibrationError("calibrated_at must not be null")

    identity = _strict_mapping(profile.get("identity"), IDENTITY_KEYS, "identity")
    _strict_mapping(identity.get("camera"), CAMERA_IDENTITY_KEYS, "identity.camera")
    _strict_mapping(identity.get("stream"), STREAM_IDENTITY_KEYS, "identity.stream")
    expected_identity = camera_acquisition_identity(camera)
    if identity != expected_identity:
        raise CameraCalibrationError(
            "calibration profile camera/ROI identity does not match system config"
        )
    expected_identity_hash = acquisition_identity_sha256(identity)
    if profile.get("identity_sha256") != expected_identity_hash:
        raise CameraCalibrationError("calibration profile identity SHA-256 mismatch")

    target_reference = _strict_mapping(
        profile.get("target"),
        TARGET_REFERENCE_KEYS,
        "target reference",
    )
    target = load_calibration_target(target_path)
    if not target.dimensions_verified:
        raise CameraCalibrationError("calibration profile target is not verified")
    if target_reference.get("target_id") != target.target_id:
        raise CameraCalibrationError("calibration profile target_id mismatch")
    if target_reference.get("target_sha256") != sha256_file(Path(target_path)):
        raise CameraCalibrationError("calibration profile target SHA-256 mismatch")

    calibration_reference = _strict_mapping(
        profile.get("calibration"),
        CALIBRATION_REFERENCE_KEYS,
        "calibration reference",
    )
    if calibration_reference.get("camera_info_sha256") != sha256_file(
        Path(calibration_path)
    ):
        raise CameraCalibrationError("calibration profile CameraInfo SHA-256 mismatch")
    archive_sha = calibration_reference.get("source_archive_sha256")
    if not isinstance(archive_sha, str) or SHA256_PATTERN.fullmatch(archive_sha) is None:
        raise CameraCalibrationError("source archive SHA-256 is invalid")
    archive_name = calibration_reference.get("source_archive_name")
    if (
        not isinstance(archive_name, str)
        or not archive_name
        or Path(archive_name).name != archive_name
        or not archive_name.endswith(".tar.gz")
    ):
        raise CameraCalibrationError("source archive name is invalid")
    upstream = calibration_reference.get("upstream_package")
    if not isinstance(upstream, str) or not upstream:
        raise CameraCalibrationError("upstream calibration package is missing")

    quality = _strict_mapping(profile.get("quality"), QUALITY_KEYS, "quality")
    _positive_integer(quality.get("sample_count"), "quality.sample_count")
    return profile


def _refuse_existing_outputs(paths: tuple[Path, ...]) -> None:
    resolved = [path.resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise CameraCalibrationError("calibration output paths must be distinct")
    for path in paths:
        if path.exists() or path.is_symlink():
            raise CameraCalibrationError(f"refusing to overwrite calibration output: {path}")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_stream, os.fdopen(
            descriptor, "wb"
        ) as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def import_calibration_archive(
    archive_path: Path | str,
    target_path: Path | str,
    calibration_output: Path | str,
    profile_output: Path | str,
    archive_output: Path | str,
    *,
    camera: dict[str, Any],
    profile_id: str,
    calibrated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Import one upstream archive without extraction and preserve its provenance."""
    if TARGET_ID_PATTERN.fullmatch(profile_id) is None:
        raise CameraCalibrationError("profile_id is not a valid versioned identifier")
    timestamp = calibrated_at or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    _utc_timestamp(timestamp, "calibrated_at")
    target_source = Path(target_path)
    target = load_calibration_target(target_source)
    if not target.dimensions_verified:
        raise CameraCalibrationError("cannot import against an unverified target")
    archive_source = Path(archive_path)
    saved = read_calibration_archive(archive_source)
    identity = camera_acquisition_identity(camera)

    calibration_destination = Path(calibration_output)
    profile_destination = Path(profile_output)
    archive_destination = Path(archive_output)
    _refuse_existing_outputs(
        (calibration_destination, profile_destination, archive_destination)
    )
    if archive_source.resolve() in {
        calibration_destination.resolve(),
        profile_destination.resolve(),
        archive_destination.resolve(),
    }:
        raise CameraCalibrationError("source archive cannot also be an output path")

    calibration_destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, validation_name = tempfile.mkstemp(
        prefix=".camera-info-validation.",
        suffix=".yaml",
        dir=calibration_destination.parent,
    )
    validation_path = Path(validation_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(saved.camera_info)
        validate_camera_calibration(validation_path, camera)
    finally:
        validation_path.unlink(missing_ok=True)

    try:
        upstream_version = importlib.metadata.version("camera-calibration")
    except importlib.metadata.PackageNotFoundError:
        upstream_version = "unknown"
    profile = {
        "schema_version": "1.0",
        "profile_id": profile_id,
        "calibrated_at": timestamp,
        "identity": identity,
        "identity_sha256": acquisition_identity_sha256(identity),
        "target": {
            "target_id": target.target_id,
            "target_sha256": sha256_file(target_source),
        },
        "calibration": {
            "camera_info_sha256": hashlib.sha256(saved.camera_info).hexdigest(),
            "source_archive_sha256": sha256_file(archive_source),
            "source_archive_name": archive_destination.name,
            "upstream_package": f"camera_calibration {upstream_version}",
        },
        "quality": {"sample_count": len(saved.image_names)},
    }
    profile_bytes = yaml.safe_dump(
        profile,
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")

    _atomic_copy(archive_source, archive_destination)
    _atomic_write(calibration_destination, saved.camera_info)
    _atomic_write(profile_destination, profile_bytes)
    load_calibration_profile(
        profile_destination,
        camera=camera,
        calibration_path=calibration_destination,
        target_path=target_source,
    )
    return {
        "schema_version": "1.0",
        "valid": True,
        "profile_id": profile_id,
        "calibration_path": str(calibration_destination),
        "profile_path": str(profile_destination),
        "source_archive_path": str(archive_destination),
        "sample_count": len(saved.image_names),
        "identity_sha256": profile["identity_sha256"],
        "camera_info_sha256": profile["calibration"]["camera_info_sha256"],
        "source_archive_sha256": profile["calibration"]["source_archive_sha256"],
    }


def _system_camera(config_path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        section = document["hik_camera_node"]
        camera = section.get("ros__parameters", section)
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise CameraCalibrationError(f"cannot load camera system config: {exc}") from exc
    if not isinstance(camera, dict):
        raise CameraCalibrationError("hik_camera_node parameters must be a mapping")
    return camera


def _camera_namespace(image_topic: str, info_topic: str) -> str:
    image_parent = str(PurePosixPath(image_topic).parent)
    info_parent = str(PurePosixPath(info_topic).parent)
    if image_parent != info_parent or not image_parent.startswith("/"):
        raise CameraCalibrationError(
            "camera image and CameraInfo topics must share one absolute namespace"
        )
    return image_parent


def build_calibration_command(
    target: CalibrationTarget,
    camera: dict[str, Any],
    *,
    require_verified_dimensions: bool = True,
) -> list[str]:
    """Build the argv-only ROS calibration command for one exact target."""
    if require_verified_dimensions and not target.dimensions_verified:
        raise CameraCalibrationError(
            "target dimensions are unverified; measure the printed board before calibration"
        )
    image_topic = camera.get("image_topic")
    info_topic = camera.get("camera_info_topic")
    camera_name = camera.get("camera_name")
    if not all(isinstance(item, str) and item for item in (image_topic, info_topic, camera_name)):
        raise CameraCalibrationError("camera topics and camera_name must be configured")
    camera_namespace = _camera_namespace(image_topic, info_topic)
    cli_pattern = "chessboard" if target.pattern == "checkerboard" else target.pattern
    command = [
        "ros2",
        "run",
        "camera_calibration",
        "cameracalibrator",
        "--pattern",
        cli_pattern,
        "--size",
        f"{target.columns}x{target.rows}",
        "--square",
        str(target.square_size_m),
        "--camera_name",
        camera_name,
        "--no-service-check",
    ]
    if target.pattern == "charuco":
        command.extend(
            [
                "--charuco_marker_size",
                str(target.marker_size_m),
                "--aruco_dict",
                str(target.aruco_dictionary),
            ]
        )
    command.extend(
        [
            "--ros-args",
            "--remap",
            f"image:={image_topic}",
            "--remap",
            f"camera:={camera_namespace}",
        ]
    )
    return command


def _matrix(document: dict[str, Any], key: str, rows: int, cols: int) -> list[float]:
    value = document.get(key)
    if not isinstance(value, dict) or value.get("rows") != rows or value.get("cols") != cols:
        raise CameraCalibrationError(f"{key} must be a {rows}x{cols} matrix")
    data = value.get("data")
    if not isinstance(data, list) or len(data) != rows * cols:
        raise CameraCalibrationError(f"{key}.data must contain {rows * cols} values")
    numbers: list[float] = []
    for item in data:
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(item)
        ):
            raise CameraCalibrationError(f"{key}.data must contain finite numbers")
        numbers.append(float(item))
    return numbers


def validate_camera_calibration(
    calibration_path: Path | str,
    camera: dict[str, Any],
) -> dict[str, Any]:
    """Validate a camera_info_manager YAML against the deployed camera identity."""
    source = Path(calibration_path)
    if not source.is_file() or source.is_symlink():
        raise CameraCalibrationError(f"calibration must be a regular file: {source}")
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CameraCalibrationError(f"cannot load camera calibration: {exc}") from exc
    if not isinstance(document, dict):
        raise CameraCalibrationError("camera calibration must be a mapping")
    width = document.get("image_width")
    height = document.get("image_height")
    expected_width = camera.get("width")
    expected_height = camera.get("height")
    if width != expected_width or height != expected_height:
        raise CameraCalibrationError(
            f"calibration dimensions {width}x{height} do not match "
            f"configured {expected_width}x{expected_height}"
        )
    if document.get("camera_name") != camera.get("camera_name"):
        raise CameraCalibrationError("calibration camera_name does not match system config")
    distortion_model = document.get("distortion_model")
    if not isinstance(distortion_model, str) or not distortion_model:
        raise CameraCalibrationError("distortion_model must be non-empty")
    camera_matrix = _matrix(document, "camera_matrix", 3, 3)
    rectification = _matrix(document, "rectification_matrix", 3, 3)
    projection = _matrix(document, "projection_matrix", 3, 4)
    distortion = document.get("distortion_coefficients")
    if not isinstance(distortion, dict) or distortion.get("rows") != 1:
        raise CameraCalibrationError("distortion_coefficients must be a 1xN matrix")
    distortion_cols = distortion.get("cols")
    if (
        not isinstance(distortion_cols, int)
        or isinstance(distortion_cols, bool)
        or distortion_cols < 4
    ):
        raise CameraCalibrationError("distortion_coefficients must contain at least 4 values")
    distortion_values = distortion.get("data")
    if not isinstance(distortion_values, list) or len(distortion_values) != distortion_cols:
        raise CameraCalibrationError("distortion_coefficients.data length mismatch")
    if any(
        not isinstance(item, (int, float))
        or isinstance(item, bool)
        or not math.isfinite(item)
        for item in distortion_values
    ):
        raise CameraCalibrationError("distortion coefficients must be finite")
    if camera_matrix[0] <= 0 or camera_matrix[4] <= 0 or camera_matrix[8] != 1.0:
        raise CameraCalibrationError("camera_matrix has an invalid pinhole model")
    if not (0 <= camera_matrix[2] <= width and 0 <= camera_matrix[5] <= height):
        raise CameraCalibrationError("camera principal point is outside the image")
    if projection[0] <= 0 or projection[5] <= 0 or projection[10] != 1.0:
        raise CameraCalibrationError("projection_matrix has an invalid pinhole model")
    if rectification == [0.0] * 9:
        raise CameraCalibrationError("rectification_matrix must not be all zero")
    return {
        "schema_version": "1.0",
        "valid": True,
        "camera_name": document["camera_name"],
        "image_width": width,
        "image_height": height,
        "distortion_model": distortion_model,
        "sha256": sha256_file(source),
    }


def _default_system_config() -> Path:
    return Path(get_package_share_directory("scout_bringup")) / "config/system.yaml"


def main(argv: Optional[list[str]] = None) -> int:
    """Run calibration or validate its CameraInfoManager output."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    run = subparsers.add_parser("run", help="launch ROS camera_calibration")
    run.add_argument("target", type=Path)
    run.add_argument("--config", type=Path, default=_default_system_config())
    run.add_argument("--dry-run", action="store_true")
    validate = subparsers.add_parser("validate", help="validate generated camera YAML")
    validate.add_argument("calibration", type=Path)
    validate.add_argument("--config", type=Path, default=_default_system_config())
    validate.add_argument("--profile", type=Path)
    validate.add_argument("--target", type=Path)
    ingest = subparsers.add_parser(
        "import",
        help="safely import /tmp/calibrationdata.tar.gz and bind it to this camera ROI",
    )
    ingest.add_argument("archive", type=Path)
    ingest.add_argument("target", type=Path)
    ingest.add_argument("calibration_output", type=Path)
    ingest.add_argument("--profile-output", type=Path)
    ingest.add_argument("--archive-output", type=Path)
    ingest.add_argument("--profile-id", required=True)
    ingest.add_argument("--calibrated-at")
    ingest.add_argument("--config", type=Path, default=_default_system_config())
    args = parser.parse_args(argv)
    try:
        camera = _system_camera(args.config)
        if args.action == "run":
            target = load_calibration_target(args.target)
            command = build_calibration_command(
                target,
                camera,
                require_verified_dimensions=not args.dry_run,
            )
            print(shlex.join(command))
            if args.dry_run:
                return 0
            return subprocess.run(command, check=False).returncode
        if args.action == "import":
            profile_output = args.profile_output or args.calibration_output.with_suffix(
                ".profile.yaml"
            )
            archive_output = args.archive_output or args.calibration_output.with_suffix(
                ".source.tar.gz"
            )
            report = import_calibration_archive(
                args.archive,
                args.target,
                args.calibration_output,
                profile_output,
                archive_output,
                camera=camera,
                profile_id=args.profile_id,
                calibrated_at=args.calibrated_at,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        report = validate_camera_calibration(args.calibration, camera)
        if bool(args.profile) != bool(args.target):
            raise CameraCalibrationError(
                "--profile and --target must be supplied together"
            )
        if args.profile is not None:
            profile = load_calibration_profile(
                args.profile,
                camera=camera,
                calibration_path=args.calibration,
                target_path=args.target,
            )
            report["profile_id"] = profile["profile_id"]
            report["identity_sha256"] = profile["identity_sha256"]
            report["profile_sha256"] = sha256_file(args.profile)
    except CameraCalibrationError as exc:
        print(json.dumps({"schema_version": "1.0", "valid": False, "error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
