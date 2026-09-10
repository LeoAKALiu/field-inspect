"""Pure transform math and fail-closed checks for FAST-LIO normalization."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

Vector3 = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]


@dataclass(frozen=True)
class Transform3:
    """Rigid transform represented as translation and xyzw quaternion."""

    translation: Vector3
    rotation: Quaternion


@dataclass(frozen=True)
class LocalizationConfig:
    """Safety-relevant localization adapter configuration."""

    input_global_frame: str = "camera_init"
    body_frame: str = "body"
    pose_timeout_ms: int = 500
    future_tolerance_ms: int = 50
    max_translation_jump_m: float = 0.5
    max_rotation_jump_rad: float = 0.35
    healthy_confirmations: int = 3
    extrinsics_verified: bool = False


@dataclass(frozen=True)
class LocalizationSample:
    """One synchronized set of transforms at the FAST-LIO pose timestamp."""

    stamp_ns: int
    now_ns: int
    parent_frame: str
    child_frame: str
    map_to_body: Transform3
    base_to_body: Optional[Transform3]
    odom_to_base: Optional[Transform3]


@dataclass(frozen=True)
class LocalizationOutcome:
    """Result consumed by the ROS wrapper and diagnostics."""

    healthy: bool
    reason: str
    map_to_odom: Optional[Transform3]
    confirmations: int


def _quaternion_norm(rotation: Quaternion) -> float:
    return math.sqrt(sum(value * value for value in rotation))


def _normalized(rotation: Quaternion) -> Quaternion:
    norm = _quaternion_norm(rotation)
    if norm == 0.0:
        raise ValueError("zero quaternion")
    return tuple(value / norm for value in rotation)  # type: ignore[return-value]


def _conjugate(rotation: Quaternion) -> Quaternion:
    x, y, z, w = rotation
    return (-x, -y, -z, w)


def _multiply(left: Quaternion, right: Quaternion) -> Quaternion:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def _rotate(rotation: Quaternion, vector: Vector3) -> Vector3:
    normalized = _normalized(rotation)
    vector_quaternion = (vector[0], vector[1], vector[2], 0.0)
    rotated = _multiply(
        _multiply(normalized, vector_quaternion),
        _conjugate(normalized),
    )
    return (rotated[0], rotated[1], rotated[2])


def compose(left: Transform3, right: Transform3) -> Transform3:
    """Return ``left * right`` using parent-to-child transform notation."""
    rotated = _rotate(left.rotation, right.translation)
    translation = tuple(
        left.translation[index] + rotated[index] for index in range(3)
    )
    rotation = _normalized(_multiply(left.rotation, right.rotation))
    return Transform3(translation=translation, rotation=rotation)


def inverse(transform: Transform3) -> Transform3:
    """Return the rigid inverse of ``transform``."""
    rotation = _conjugate(_normalized(transform.rotation))
    translation = _rotate(
        rotation,
        tuple(-value for value in transform.translation),
    )
    return Transform3(translation=translation, rotation=rotation)


def _is_valid_transform(transform: Transform3) -> bool:
    values = (*transform.translation, *transform.rotation)
    if not all(math.isfinite(value) for value in values):
        return False
    norm = _quaternion_norm(transform.rotation)
    return 0.95 <= norm <= 1.05


def _translation_distance(left: Transform3, right: Transform3) -> float:
    return math.sqrt(
        sum(
            (left.translation[index] - right.translation[index]) ** 2
            for index in range(3)
        )
    )


def _rotation_distance(left: Transform3, right: Transform3) -> float:
    left_rotation = _normalized(left.rotation)
    right_rotation = _normalized(right.rotation)
    dot = abs(
        sum(left_rotation[index] * right_rotation[index] for index in range(4))
    )
    return 2.0 * math.acos(min(1.0, max(-1.0, dot)))


class LocalizationCore:
    """Stateful continuity and recovery checks around deterministic TF composition."""

    def __init__(self, config: LocalizationConfig) -> None:
        self._config = config
        self._last_pose: Optional[Transform3] = None
        self._last_output: Optional[Transform3] = None
        self._last_stamp_ns: Optional[int] = None
        self._confirmations = 0

    @property
    def confirmations(self) -> int:
        """Return the number of consecutive valid pose samples."""
        return self._confirmations

    def reset(self) -> None:
        """Require a fresh sequence of valid samples after an external timeout."""
        self._last_pose = None
        self._last_output = None
        self._last_stamp_ns = None
        self._confirmations = 0

    def _unhealthy(
        self,
        reason: str,
        *,
        baseline: Optional[LocalizationSample] = None,
        baseline_output: Optional[Transform3] = None,
    ) -> LocalizationOutcome:
        self.reset()
        if baseline is not None:
            self._last_pose = baseline.map_to_body
            self._last_output = baseline_output
            self._last_stamp_ns = baseline.stamp_ns
            self._confirmations = 1
        return LocalizationOutcome(False, reason, None, self._confirmations)

    def evaluate(self, sample: LocalizationSample) -> LocalizationOutcome:
        """Validate and compose one synchronized localization sample."""
        if not self._config.extrinsics_verified:
            return self._unhealthy("extrinsics_unverified")
        if sample.parent_frame != self._config.input_global_frame:
            return self._unhealthy("parent_frame_mismatch")
        if sample.child_frame != self._config.body_frame:
            return self._unhealthy("child_frame_mismatch")
        if sample.stamp_ns <= 0:
            return self._unhealthy("pose_timestamp_invalid")

        age_ns = sample.now_ns - sample.stamp_ns
        if age_ns < -self._config.future_tolerance_ms * 1_000_000:
            return self._unhealthy("pose_from_future")
        if age_ns > self._config.pose_timeout_ms * 1_000_000:
            return self._unhealthy("pose_stale")
        if not _is_valid_transform(sample.map_to_body):
            return self._unhealthy("pose_transform_invalid")
        if sample.base_to_body is None:
            return self._unhealthy("body_extrinsic_missing")
        if not _is_valid_transform(sample.base_to_body):
            return self._unhealthy("body_extrinsic_invalid")
        if sample.odom_to_base is None:
            return self._unhealthy("odom_transform_missing")
        if not _is_valid_transform(sample.odom_to_base):
            return self._unhealthy("odom_transform_invalid")

        map_to_base = compose(sample.map_to_body, inverse(sample.base_to_body))
        map_to_odom = compose(map_to_base, inverse(sample.odom_to_base))
        if not _is_valid_transform(map_to_odom):
            return self._unhealthy("output_transform_invalid")

        if self._last_stamp_ns is not None and sample.stamp_ns <= self._last_stamp_ns:
            return self._unhealthy("pose_timestamp_non_monotonic")
        if self._last_pose is not None:
            if (
                _translation_distance(sample.map_to_body, self._last_pose)
                > self._config.max_translation_jump_m
            ):
                return self._unhealthy(
                    "position_jump",
                    baseline=sample,
                    baseline_output=map_to_odom,
                )
            if (
                _rotation_distance(sample.map_to_body, self._last_pose)
                > self._config.max_rotation_jump_rad
            ):
                return self._unhealthy(
                    "orientation_jump",
                    baseline=sample,
                    baseline_output=map_to_odom,
                )
        if self._last_output is not None:
            if (
                _translation_distance(map_to_odom, self._last_output)
                > self._config.max_translation_jump_m
            ):
                return self._unhealthy(
                    "map_to_odom_position_jump",
                    baseline=sample,
                    baseline_output=map_to_odom,
                )
            if (
                _rotation_distance(map_to_odom, self._last_output)
                > self._config.max_rotation_jump_rad
            ):
                return self._unhealthy(
                    "map_to_odom_orientation_jump",
                    baseline=sample,
                    baseline_output=map_to_odom,
                )

        self._last_pose = sample.map_to_body
        self._last_output = map_to_odom
        self._last_stamp_ns = sample.stamp_ns
        self._confirmations += 1
        if self._confirmations < self._config.healthy_confirmations:
            return LocalizationOutcome(
                False,
                "warming_up",
                None,
                self._confirmations,
            )
        return LocalizationOutcome(
            True,
            "healthy",
            map_to_odom,
            self._confirmations,
        )
