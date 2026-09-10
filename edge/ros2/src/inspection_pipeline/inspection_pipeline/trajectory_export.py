"""Generate a scene-aligned replay trajectory from a finalized MCAP run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import rosbag2_py
import yaml
from ament_index_python.packages import get_package_share_directory
from nav_msgs.msg import Odometry
from rclpy.serialization import deserialize_message
from scipy.spatial.transform import Rotation

from inspection_pipeline.trajectory import (
    PoseSample,
    RigidTransform,
    SceneAlignment,
    SpeedSample,
    TrajectoryError,
    build_trajectory,
    validate_transform,
)

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TERMINAL_STATUSES = {
    "completed",
    "completed_with_exceptions",
    "incomplete",
    "aborted",
    "failed",
}
ALIGNMENT_SOURCE_KINDS = {"onsite_verified", "synthetic_contract_fixture"}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrajectoryError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TrajectoryError(f"{path} must contain a JSON object")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise TrajectoryError(f"{path} must be a regular alignment/config file")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise TrajectoryError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TrajectoryError(f"{path} must contain a YAML mapping")
    return value


def _parameters(document: dict[str, Any], section: str) -> dict[str, Any]:
    value = document.get(section)
    if not isinstance(value, dict):
        raise TrajectoryError(f"system.yaml is missing {section}")
    parameters = value.get("ros__parameters", value)
    if not isinstance(parameters, dict):
        raise TrajectoryError(f"system.yaml section {section} is invalid")
    return parameters


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < minimum
    ):
        raise TrajectoryError(f"{field} must be a finite number >= {minimum}")
    return float(value)


def _vector(value: Any, size: int, field: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != size:
        raise TrajectoryError(f"{field} must contain {size} numbers")
    return tuple(_number(item, field, minimum=-math.inf) for item in value)


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise TrajectoryError(f"{field} is not a valid identifier")
    return value


def _utc(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TrajectoryError(f"{field} must be an ISO 8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrajectoryError(f"{field} must be an ISO 8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise TrajectoryError(f"{field} must use UTC")
    return value


def load_scene_alignment(
    path: Path,
    *,
    allow_synthetic: bool = False,
) -> tuple[SceneAlignment, dict[str, Any]]:
    """Load a verified, quality-gated scene alignment YAML."""
    document = _load_yaml(path)
    if document.get("schema_version") != "1.0":
        raise TrajectoryError("scene alignment schema_version must be 1.0")
    source_kind = document.get("source_kind")
    if source_kind not in ALIGNMENT_SOURCE_KINDS:
        raise TrajectoryError(
            f"scene alignment source_kind must be one of {sorted(ALIGNMENT_SOURCE_KINDS)}"
        )
    if source_kind == "synthetic_contract_fixture" and not allow_synthetic:
        raise TrajectoryError(
            "synthetic scene alignment is disabled; it cannot be field evidence"
        )
    if document.get("verified") is not True:
        raise TrajectoryError("scene alignment must be explicitly verified")
    _utc(document.get("verified_at"), "scene alignment verified_at")
    for field in ("method", "source_reference"):
        value = document.get(field)
        if not isinstance(value, str) or not value.strip():
            raise TrajectoryError(f"scene alignment {field} must not be empty")

    quality = document.get("quality")
    if not isinstance(quality, dict):
        raise TrajectoryError("scene alignment quality must be a mapping")
    position_residual = _number(
        quality.get("max_position_residual_m"),
        "quality.max_position_residual_m",
    )
    position_limit = _number(
        quality.get("acceptance_position_m"),
        "quality.acceptance_position_m",
    )
    heading_residual = _number(
        quality.get("max_heading_residual_deg"),
        "quality.max_heading_residual_deg",
    )
    heading_limit = _number(
        quality.get("acceptance_heading_deg"),
        "quality.acceptance_heading_deg",
    )
    up_axis_residual = _number(
        quality.get("max_up_axis_error_deg"),
        "quality.max_up_axis_error_deg",
    )
    up_axis_limit = _number(
        quality.get("acceptance_up_axis_error_deg"),
        "quality.acceptance_up_axis_error_deg",
    )
    if position_limit <= 0 or heading_limit <= 0 or up_axis_limit <= 0:
        raise TrajectoryError("scene alignment acceptance limits must be positive")
    if position_residual > position_limit:
        raise TrajectoryError("scene alignment position residual exceeds acceptance")
    if heading_residual > heading_limit:
        raise TrajectoryError("scene alignment heading residual exceeds acceptance")
    if up_axis_residual > up_axis_limit:
        raise TrajectoryError("scene alignment up-axis residual exceeds acceptance")

    transform_value = document.get("transform")
    if not isinstance(transform_value, dict):
        raise TrajectoryError("scene alignment transform must be a mapping")
    transform = RigidTransform(
        _vector(transform_value.get("translation_m"), 3, "transform.translation_m"),
        _vector(transform_value.get("rotation_xyzw"), 4, "transform.rotation_xyzw"),
    )
    validate_transform(transform, "scene alignment transform")
    mapped_up = Rotation.from_quat(transform.rotation).apply((0.0, 0.0, 1.0))
    up_dot = min(1.0, max(-1.0, float(mapped_up[1])))
    transform_up_error_deg = math.degrees(math.acos(up_dot))
    if transform_up_error_deg > up_axis_limit:
        raise TrajectoryError(
            "scene alignment rotation does not map map +Z to scene +Y"
        )
    source_sha256 = _sha256(path)
    alignment = SceneAlignment(
        alignment_id=_identifier(document.get("alignment_id"), "alignment_id"),
        source_kind=source_kind,
        input_frame=str(document.get("input_frame", "")),
        output_coordinate_system=str(document.get("output_coordinate_system", "")),
        transform=transform,
        source_sha256=source_sha256,
    )
    if not alignment.input_frame:
        raise TrajectoryError("scene alignment input_frame must not be empty")
    if alignment.output_coordinate_system != "scene_local_yup":
        raise TrajectoryError("scene alignment output must be scene_local_yup")
    return alignment, document


def _load_run_contract(
    run_dir: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    RigidTransform,
]:
    operational = _load_json(run_dir / "manifest.json")
    run_id = _identifier(operational.get("run_id"), "run_id")
    if operational.get("status") not in TERMINAL_STATUSES:
        raise TrajectoryError(f"run {run_id} is not finalized")
    system_path = run_dir / "config/system.yaml"
    system = _load_yaml(system_path)
    if operational.get("config_sha256") != _sha256(system_path):
        raise TrajectoryError("manifest config_sha256 does not match config/system.yaml")
    bringup = _parameters(system, "scout_bringup")
    localization = _parameters(system, "localization_adapter")
    health = _parameters(system, "health_monitor")
    if bringup.get("extrinsics_verified") is not True:
        raise TrajectoryError("run extrinsics_verified must be true")
    if localization.get("base_frame") != bringup.get("base_frame"):
        raise TrajectoryError("localization base_frame does not match scout_bringup")
    if localization.get("body_frame") != bringup.get("body_frame"):
        raise TrajectoryError("localization body_frame does not match scout_bringup")
    base_to_body = RigidTransform(
        _vector(
            bringup.get("base_to_body_xyz_m"),
            3,
            "scout_bringup.base_to_body_xyz_m",
        ),
        tuple(
            float(value)
            for value in Rotation.from_euler(
                "xyz",
                _vector(
                    bringup.get("base_to_body_rpy_rad"),
                    3,
                    "scout_bringup.base_to_body_rpy_rad",
                ),
            ).as_quat()
        ),
    )
    validate_transform(base_to_body, "base_to_body")
    return operational, bringup, localization, health, base_to_body


def _stamp_ns(message: Odometry) -> int:
    return int(message.header.stamp.sec) * 1_000_000_000 + int(
        message.header.stamp.nanosec
    )


def _read_odometry_samples(
    bag_dir: Path,
    *,
    pose_topic: str,
    speed_topic: Optional[str],
    odom_frame: str,
    base_frame: str,
    storage_id: str,
    max_pose_samples: int = 1_000_000,
    max_speed_samples: int = 1_000_000,
) -> tuple[list[PoseSample], list[SpeedSample]]:
    if max_pose_samples <= 0 or max_speed_samples <= 0:
        raise TrajectoryError("odometry sample limits must be positive")
    if not (bag_dir / "metadata.yaml").is_file():
        raise TrajectoryError(f"bag metadata is missing: {bag_dir / 'metadata.yaml'}")
    reader = rosbag2_py.SequentialReader()
    try:
        reader.open(
            rosbag2_py.StorageOptions(uri=str(bag_dir), storage_id=storage_id),
            rosbag2_py.ConverterOptions("", ""),
        )
    except RuntimeError as exc:
        raise TrajectoryError(f"cannot open MCAP bag {bag_dir}: {exc}") from exc
    topic_types = {item.name: item.type for item in reader.get_all_topics_and_types()}
    if topic_types.get(pose_topic) != "nav_msgs/msg/Odometry":
        raise TrajectoryError(
            f"{pose_topic} must exist with type nav_msgs/msg/Odometry"
        )
    if (
        speed_topic is not None
        and speed_topic in topic_types
        and topic_types[speed_topic] != "nav_msgs/msg/Odometry"
    ):
        raise TrajectoryError(
            f"{speed_topic} must have type nav_msgs/msg/Odometry when present"
        )

    poses = []
    speeds = []
    selected_topics = {pose_topic}
    if speed_topic is not None:
        selected_topics.add(speed_topic)
    reader.set_filter(rosbag2_py.StorageFilter(topics=sorted(selected_topics)))
    while reader.has_next():
        topic, serialized, _received_stamp = reader.read_next()
        if topic not in selected_topics:
            continue
        message = deserialize_message(serialized, Odometry)
        stamp_ns = _stamp_ns(message)
        if topic == pose_topic:
            pose = message.pose.pose
            poses.append(
                PoseSample(
                    stamp_ns=stamp_ns,
                    parent_frame=message.header.frame_id,
                    child_frame=message.child_frame_id,
                    map_to_body=RigidTransform(
                        (pose.position.x, pose.position.y, pose.position.z),
                        (
                            pose.orientation.x,
                            pose.orientation.y,
                            pose.orientation.z,
                            pose.orientation.w,
                        ),
                    ),
                )
            )
            if len(poses) > max_pose_samples:
                raise TrajectoryError("pose sample count exceeds configured limit")
        else:
            if message.header.frame_id != odom_frame:
                raise TrajectoryError(
                    f"{speed_topic} frame {message.header.frame_id!r} does not match "
                    f"{odom_frame!r}"
                )
            if message.child_frame_id != base_frame:
                raise TrajectoryError(
                    f"{speed_topic} child frame {message.child_frame_id!r} does not "
                    f"match {base_frame!r}"
                )
            linear = message.twist.twist.linear
            speed = math.hypot(float(linear.x), float(linear.y))
            speeds.append(SpeedSample(stamp_ns, speed))
            if len(speeds) > max_speed_samples:
                raise TrajectoryError("speed sample count exceeds configured limit")
    return poses, speeds


def _json_bytes(value: dict[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TrajectoryError(f"cannot serialize trajectory output: {exc}") from exc


def _write_atomic(path: Path, content: bytes, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise TrajectoryError(f"output is not a regular file: {path}")
        if path.read_bytes() == content:
            return
        if not replace:
            raise TrajectoryError(f"output already exists with different content: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def export_trajectory(
    run_dir: Path | str,
    alignment_path: Path | str,
    *,
    max_speed_skew_ms: Optional[int] = None,
    allow_synthetic_alignment: bool = False,
    allow_non_mcap_bag: bool = False,
    replace: bool = False,
) -> Path:
    """Generate trajectory.json and provenance from one finalized inspection run."""
    unresolved_source = Path(run_dir).expanduser()
    if unresolved_source.is_symlink():
        raise TrajectoryError(f"run directory must not be a symbolic link: {run_dir}")
    source = unresolved_source.resolve()
    if not source.is_dir():
        raise TrajectoryError(f"run directory does not exist: {source}")
    unresolved_alignment = Path(alignment_path).expanduser()
    if unresolved_alignment.is_symlink():
        raise TrajectoryError(
            f"scene alignment must not be a symbolic link: {alignment_path}"
        )
    alignment_source = unresolved_alignment.resolve()
    operational, _bringup, localization, health, base_to_body = (
        _load_run_contract(source)
    )
    alignment, alignment_document = load_scene_alignment(
        alignment_source,
        allow_synthetic=allow_synthetic_alignment,
    )
    if alignment.input_frame != localization.get("map_frame"):
        raise TrajectoryError(
            "scene alignment input_frame must match localization_adapter.map_frame"
        )

    run_id = str(operational["run_id"])
    bag_reference = operational.get("bag")
    if not isinstance(bag_reference, dict):
        raise TrajectoryError("operational manifest bag must be a mapping")
    storage_id = str(bag_reference.get("storage_id", ""))
    if storage_id != "mcap" and not allow_non_mcap_bag:
        raise TrajectoryError("inspection trajectory export requires MCAP storage")
    pose_topic = str(localization.get("input_odom_topic", ""))
    speed_topic = (
        str(health.get("scout_odom_topic", ""))
        if max_speed_skew_ms is not None
        else None
    )
    if speed_topic is not None and not speed_topic.startswith("/"):
        raise TrajectoryError("health_monitor.scout_odom_topic must be absolute")
    poses, speeds = _read_odometry_samples(
        source / "bag" / run_id,
        pose_topic=pose_topic,
        speed_topic=speed_topic,
        odom_frame=str(localization.get("odom_frame", "")),
        base_frame=str(localization.get("base_frame", "")),
        storage_id=storage_id,
    )
    trajectory = build_trajectory(
        run_id=run_id,
        alignment=alignment,
        poses=poses,
        base_to_body=base_to_body,
        input_global_frame=str(localization.get("input_global_frame", "")),
        body_frame=str(localization.get("body_frame", "")),
        speed_samples=speeds,
        max_speed_skew_ms=max_speed_skew_ms,
    )
    trajectory_bytes = _json_bytes(trajectory)
    matched_speeds = sum(
        point["speed_mps"] is not None for point in trajectory["points"]
    )
    contract_lock = _load_json(
        Path(get_package_share_directory("inspection_pipeline"))
        / "config/run-bundle.lock.json"
    )
    provenance = {
        "schema_version": "1.0",
        "run_id": run_id,
        "coordinate_system": "scene_local_yup",
        "trajectory_sha256": _sha256_bytes(trajectory_bytes),
        "point_count": len(trajectory["points"]),
        "first_pose_stamp_ns": poses[0].stamp_ns,
        "last_pose_stamp_ns": poses[-1].stamp_ns,
        "pose_topic": pose_topic,
        "pose_type": "nav_msgs/msg/Odometry",
        "pose_parent_frame": str(localization.get("input_global_frame", "")),
        "body_frame": str(localization.get("body_frame", "")),
        "map_frame": str(localization.get("map_frame", "")),
        "base_frame": str(localization.get("base_frame", "")),
        "bag_storage_id": storage_id,
        "speed_topic": speed_topic,
        "speed_samples_matched": matched_speeds,
        "max_speed_skew_ms": max_speed_skew_ms,
        "extrinsics_verified": True,
        "base_to_body": {
            "translation_m": list(base_to_body.translation),
            "rotation_xyzw": list(base_to_body.rotation),
        },
        "alignment": {
            "alignment_id": alignment.alignment_id,
            "source_kind": alignment.source_kind,
            "verified": True,
            "verified_at": alignment_document["verified_at"],
            "sha256": alignment.source_sha256,
            "method": alignment_document["method"],
            "source_reference": alignment_document["source_reference"],
            "quality": alignment_document["quality"],
        },
        "contract_authority": contract_lock["authority"],
    }

    alignment_target = source / "config/scene_alignment.yaml"
    trajectory_target = source / "replay/trajectory.json"
    provenance_target = source / "replay/trajectory.provenance.json"
    _write_atomic(alignment_target, alignment_source.read_bytes(), replace=replace)
    _write_atomic(trajectory_target, trajectory_bytes, replace=replace)
    _write_atomic(provenance_target, _json_bytes(provenance), replace=replace)
    return trajectory_target


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("alignment", type=Path)
    parser.add_argument("--max-speed-skew-ms", type=int)
    parser.add_argument("--allow-synthetic-alignment", action="store_true")
    parser.add_argument("--allow-non-mcap-bag", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    output = export_trajectory(
        args.run_dir,
        args.alignment,
        max_speed_skew_ms=args.max_speed_skew_ms,
        allow_synthetic_alignment=args.allow_synthetic_alignment,
        allow_non_mcap_bag=args.allow_non_mcap_bag,
        replace=args.replace,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
