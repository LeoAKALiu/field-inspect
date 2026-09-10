"""Pure scene-alignment and replay-trajectory contract logic."""

from __future__ import annotations

import math
import re
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from scipy.spatial.transform import Rotation

Vector3 = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TrajectoryError(ValueError):
    """Input data cannot produce a trustworthy replay trajectory."""


@dataclass(frozen=True)
class RigidTransform:
    """A parent-to-child rigid transform using an xyzw quaternion."""

    translation: Vector3
    rotation: Quaternion


@dataclass(frozen=True)
class SceneAlignment:
    """Versioned, verified transform from the run map into the twin scene."""

    alignment_id: str
    source_kind: str
    input_frame: str
    output_coordinate_system: str
    transform: RigidTransform
    source_sha256: str


@dataclass(frozen=True)
class PoseSample:
    """One FAST-LIO body pose at its sensor acquisition timestamp."""

    stamp_ns: int
    parent_frame: str
    child_frame: str
    map_to_body: RigidTransform


@dataclass(frozen=True)
class SpeedSample:
    """One SCOUT planar speed sample at its ROS header timestamp."""

    stamp_ns: int
    speed_mps: float


def _finite_vector(values: Sequence[Any], size: int, field: str) -> tuple[float, ...]:
    if (
        not isinstance(values, (list, tuple))
        or len(values) != size
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            for value in values
        )
    ):
        raise TrajectoryError(f"{field} must contain {size} finite numbers")
    return tuple(float(value) for value in values)


def validate_transform(transform: RigidTransform, field: str) -> None:
    """Reject non-finite translations and invalid quaternion magnitudes."""
    _finite_vector(transform.translation, 3, f"{field}.translation")
    rotation = _finite_vector(transform.rotation, 4, f"{field}.rotation")
    norm = math.sqrt(sum(value * value for value in rotation))
    if not 0.95 <= norm <= 1.05:
        raise TrajectoryError(f"{field}.rotation quaternion norm must be 0.95-1.05")


def compose(left: RigidTransform, right: RigidTransform) -> RigidTransform:
    """Compose two transforms using SciPy's tested quaternion implementation."""
    validate_transform(left, "left")
    validate_transform(right, "right")
    left_rotation = Rotation.from_quat(left.rotation)
    right_rotation = Rotation.from_quat(right.rotation)
    translated = left_rotation.apply(right.translation) + left.translation
    rotation = (left_rotation * right_rotation).as_quat()
    return RigidTransform(
        tuple(float(value) for value in translated),
        tuple(float(value) for value in rotation),
    )


def inverse(transform: RigidTransform) -> RigidTransform:
    """Return the inverse rigid transform."""
    validate_transform(transform, "transform")
    rotation = Rotation.from_quat(transform.rotation).inv()
    translation = rotation.apply(tuple(-value for value in transform.translation))
    return RigidTransform(
        tuple(float(value) for value in translation),
        tuple(float(value) for value in rotation.as_quat()),
    )


def _iso_utc(stamp_ns: int) -> str:
    if stamp_ns <= 0:
        raise TrajectoryError("pose timestamp must be positive")
    seconds, nanoseconds = divmod(stamp_ns, 1_000_000_000)
    try:
        value = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise TrajectoryError("pose timestamp is outside the UTC range") from exc
    if value.year < 2000:
        raise TrajectoryError("pose timestamp predates year 2000")
    value = value.replace(microsecond=nanoseconds // 1000)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _nearest_speed(
    stamp_ns: int,
    samples: Sequence[SpeedSample],
    stamps: Sequence[int],
    max_skew_ns: int,
) -> Optional[float]:
    if not samples:
        return None
    index = bisect_left(stamps, stamp_ns)
    candidates = []
    if index < len(samples):
        candidates.append(samples[index])
    if index > 0:
        candidates.append(samples[index - 1])
    nearest = min(candidates, key=lambda item: abs(item.stamp_ns - stamp_ns))
    if abs(nearest.stamp_ns - stamp_ns) > max_skew_ns:
        return None
    return nearest.speed_mps


def build_trajectory(
    *,
    run_id: str,
    alignment: SceneAlignment,
    poses: Sequence[PoseSample],
    base_to_body: RigidTransform,
    input_global_frame: str,
    body_frame: str,
    speed_samples: Sequence[SpeedSample] = (),
    max_speed_skew_ms: Optional[int] = None,
) -> dict[str, Any]:
    """Build the pinned digital-twin trajectory document from recorded poses."""
    if IDENTIFIER_PATTERN.fullmatch(run_id) is None:
        raise TrajectoryError("run_id is not a valid contract identifier")
    if alignment.output_coordinate_system != "scene_local_yup":
        raise TrajectoryError("alignment output must be scene_local_yup")
    if not poses:
        raise TrajectoryError("the bag contains no FAST-LIO pose samples")
    if len(poses) > 1_000_000:
        raise TrajectoryError("trajectory exceeds the 1,000,000 point contract limit")
    if max_speed_skew_ms is not None and max_speed_skew_ms <= 0:
        raise TrajectoryError("max_speed_skew_ms must be positive")
    validate_transform(alignment.transform, "alignment.transform")
    validate_transform(base_to_body, "base_to_body")

    sorted_speeds = (
        sorted(speed_samples, key=lambda item: item.stamp_ns)
        if max_speed_skew_ms is not None
        else []
    )
    for sample in sorted_speeds:
        if sample.stamp_ns <= 0:
            raise TrajectoryError("speed timestamp must be positive")
        if not math.isfinite(sample.speed_mps) or sample.speed_mps < 0:
            raise TrajectoryError("speed_mps must be a finite non-negative number")
    speed_stamps = [sample.stamp_ns for sample in sorted_speeds]
    max_skew_ns = (max_speed_skew_ms or 0) * 1_000_000

    points = []
    previous_stamp_ns: Optional[int] = None
    for sequence, sample in enumerate(poses):
        if sample.parent_frame != input_global_frame:
            raise TrajectoryError(
                f"pose parent frame {sample.parent_frame!r} does not match "
                f"{input_global_frame!r}"
            )
        if sample.child_frame != body_frame:
            raise TrajectoryError(
                f"pose child frame {sample.child_frame!r} does not match "
                f"{body_frame!r}"
            )
        if previous_stamp_ns is not None and sample.stamp_ns <= previous_stamp_ns:
            raise TrajectoryError("pose timestamps must be strictly increasing")
        validate_transform(sample.map_to_body, f"poses[{sequence}]")

        map_to_base = compose(sample.map_to_body, inverse(base_to_body))
        scene_to_base = compose(alignment.transform, map_to_base)
        scene_rotation = Rotation.from_quat(scene_to_base.rotation)
        forward = scene_rotation.apply((1.0, 0.0, 0.0))
        horizontal_norm = math.hypot(float(forward[0]), float(forward[2]))
        if horizontal_norm < 1e-9:
            raise TrajectoryError("vehicle forward axis is vertical in scene coordinates")
        heading_deg = math.degrees(math.atan2(float(forward[2]), float(forward[0])))
        heading_deg %= 360.0
        speed = _nearest_speed(
            sample.stamp_ns,
            sorted_speeds,
            speed_stamps,
            max_skew_ns,
        )
        points.append(
            {
                "seq": sequence,
                "timestamp": _iso_utc(sample.stamp_ns),
                "position": {
                    "x": scene_to_base.translation[0],
                    "y": scene_to_base.translation[1],
                    "z": scene_to_base.translation[2],
                },
                "heading_deg": heading_deg,
                "speed_mps": speed,
            }
        )
        previous_stamp_ns = sample.stamp_ns

    document = {
        "schema_version": "1.0",
        "run_id": run_id,
        "coordinate_system": "scene_local_yup",
        "alignment_id": alignment.alignment_id,
        "points": points,
    }
    validate_trajectory_document(document)
    return document


def _utc_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise TrajectoryError(f"{field} must be an ISO 8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrajectoryError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise TrajectoryError(f"{field} must use UTC")
    return parsed


def validate_trajectory_document(document: dict[str, Any]) -> None:
    """Validate the pinned v1 trajectory subset before durable export."""
    if not isinstance(document, dict):
        raise TrajectoryError("trajectory must be a JSON object")
    if set(document) != {
        "schema_version",
        "run_id",
        "coordinate_system",
        "alignment_id",
        "points",
    }:
        raise TrajectoryError("trajectory has missing or unexpected top-level fields")
    if document["schema_version"] != "1.0":
        raise TrajectoryError("trajectory schema_version must be 1.0")
    if (
        not isinstance(document["run_id"], str)
        or IDENTIFIER_PATTERN.fullmatch(document["run_id"]) is None
    ):
        raise TrajectoryError("trajectory run_id is not a valid identifier")
    if document["coordinate_system"] != "scene_local_yup":
        raise TrajectoryError("trajectory coordinate_system must be scene_local_yup")
    if (
        not isinstance(document["alignment_id"], str)
        or IDENTIFIER_PATTERN.fullmatch(document["alignment_id"]) is None
    ):
        raise TrajectoryError("trajectory alignment_id is not a valid identifier")
    points = document["points"]
    if not isinstance(points, list) or not 1 <= len(points) <= 1_000_000:
        raise TrajectoryError("trajectory points must contain 1-1,000,000 items")

    previous_time: Optional[datetime] = None
    required = {"seq", "timestamp", "position", "heading_deg", "speed_mps"}
    for index, point in enumerate(points):
        if not isinstance(point, dict) or not required <= set(point):
            raise TrajectoryError(f"trajectory point {index} is incomplete")
        if set(point) - (required | {"battery_pct"}):
            raise TrajectoryError(f"trajectory point {index} has unexpected fields")
        if point["seq"] != index:
            raise TrajectoryError("trajectory seq must be contiguous and zero-based")
        timestamp = _utc_datetime(point["timestamp"], f"points[{index}].timestamp")
        if previous_time is not None and timestamp <= previous_time:
            raise TrajectoryError("trajectory timestamps must be strictly increasing")
        previous_time = timestamp
        position = point["position"]
        if not isinstance(position, dict) or set(position) != {"x", "y", "z"}:
            raise TrajectoryError(f"points[{index}].position must contain x/y/z")
        _finite_vector(
            [position["x"], position["y"], position["z"]],
            3,
            f"points[{index}].position",
        )
        heading = point["heading_deg"]
        if heading is not None and (
            not isinstance(heading, (int, float))
            or isinstance(heading, bool)
            or not math.isfinite(heading)
            or not 0 <= heading < 360
        ):
            raise TrajectoryError(f"points[{index}].heading_deg is invalid")
        speed = point["speed_mps"]
        if speed is not None and (
            not isinstance(speed, (int, float))
            or isinstance(speed, bool)
            or not math.isfinite(speed)
            or speed < 0
        ):
            raise TrajectoryError(f"points[{index}].speed_mps is invalid")
        battery = point.get("battery_pct")
        if battery is not None and (
            not isinstance(battery, (int, float))
            or isinstance(battery, bool)
            or not math.isfinite(battery)
            or not 0 <= battery <= 100
        ):
            raise TrajectoryError(f"points[{index}].battery_pct is invalid")
