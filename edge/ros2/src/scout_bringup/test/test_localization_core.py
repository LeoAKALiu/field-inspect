"""Tests for deterministic FAST-LIO transform normalization."""

from __future__ import annotations

import math

import pytest

from scout_bringup.localization_core import (
    LocalizationConfig,
    LocalizationCore,
    LocalizationSample,
    Transform3,
    compose,
    inverse,
)

IDENTITY = Transform3((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def sample(
    *,
    stamp_ns: int,
    x: float = 0.0,
    parent_frame: str = "camera_init",
    child_frame: str = "body",
    base_to_body: Transform3 | None = IDENTITY,
    odom_to_base: Transform3 | None = IDENTITY,
) -> LocalizationSample:
    """Return a valid localization sample with overridable failure inputs."""
    return LocalizationSample(
        stamp_ns=stamp_ns,
        now_ns=stamp_ns + 10_000_000,
        parent_frame=parent_frame,
        child_frame=child_frame,
        map_to_body=Transform3((x, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
        base_to_body=base_to_body,
        odom_to_base=odom_to_base,
    )


def ready_core(**overrides: object) -> LocalizationCore:
    """Create a core with verified extrinsics and chosen thresholds."""
    values = {"extrinsics_verified": True, "healthy_confirmations": 3, **overrides}
    return LocalizationCore(LocalizationConfig(**values))


def test_compose_and_inverse_round_trip() -> None:
    """Rigid transform helpers must preserve non-trivial rotations."""
    yaw_90 = Transform3(
        (1.0, 2.0, 0.0),
        (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)),
    )
    result = compose(yaw_90, inverse(yaw_90))
    assert result.translation == pytest.approx((0.0, 0.0, 0.0))
    assert result.rotation == pytest.approx((0.0, 0.0, 0.0, 1.0))


def test_composes_map_to_odom_after_three_confirmations() -> None:
    """The adapter implements the ADR-0004 transform equation exactly."""
    core = ready_core()
    base_to_body = Transform3((1.0, 0.0, 0.0), IDENTITY.rotation)
    odom_to_base = Transform3((2.0, 0.0, 0.0), IDENTITY.rotation)

    first = core.evaluate(
        sample(
            stamp_ns=1_000_000_000,
            x=10.0,
            base_to_body=base_to_body,
            odom_to_base=odom_to_base,
        )
    )
    second = core.evaluate(
        sample(
            stamp_ns=1_100_000_000,
            x=10.01,
            base_to_body=base_to_body,
            odom_to_base=odom_to_base,
        )
    )
    third = core.evaluate(
        sample(
            stamp_ns=1_200_000_000,
            x=10.02,
            base_to_body=base_to_body,
            odom_to_base=odom_to_base,
        )
    )

    assert first.reason == second.reason == "warming_up"
    assert third.healthy is True
    assert third.map_to_odom is not None
    assert third.map_to_odom.translation == pytest.approx((7.02, 0.0, 0.0))


@pytest.mark.parametrize(
    ("config", "message", "reason"),
    [
        (LocalizationConfig(), sample(stamp_ns=1_000_000_000), "extrinsics_unverified"),
        (
            LocalizationConfig(extrinsics_verified=True),
            sample(stamp_ns=1_000_000_000, parent_frame="map"),
            "parent_frame_mismatch",
        ),
        (
            LocalizationConfig(extrinsics_verified=True),
            sample(stamp_ns=1_000_000_000, child_frame="base_link"),
            "child_frame_mismatch",
        ),
        (
            LocalizationConfig(extrinsics_verified=True),
            sample(stamp_ns=1_000_000_000, base_to_body=None),
            "body_extrinsic_missing",
        ),
        (
            LocalizationConfig(extrinsics_verified=True),
            sample(stamp_ns=1_000_000_000, odom_to_base=None),
            "odom_transform_missing",
        ),
    ],
)
def test_rejects_invalid_contract_inputs(
    config: LocalizationConfig,
    message: LocalizationSample,
    reason: str,
) -> None:
    """Every missing prerequisite has a stable fail-closed reason."""
    outcome = LocalizationCore(config).evaluate(message)
    assert outcome.healthy is False
    assert outcome.reason == reason


def test_rejects_stale_future_and_non_monotonic_timestamps() -> None:
    """Pose time must be recent and strictly increasing."""
    core = ready_core()
    stale = sample(stamp_ns=1_000_000_000)
    stale = LocalizationSample(**{**stale.__dict__, "now_ns": 2_000_000_000})
    assert core.evaluate(stale).reason == "pose_stale"

    future = sample(stamp_ns=2_000_000_000)
    future = LocalizationSample(**{**future.__dict__, "now_ns": 1_000_000_000})
    assert core.evaluate(future).reason == "pose_from_future"

    assert core.evaluate(sample(stamp_ns=3_000_000_000)).reason == "warming_up"
    assert core.evaluate(sample(stamp_ns=3_000_000_000)).reason == (
        "pose_timestamp_non_monotonic"
    )


def test_jump_resets_health_and_requires_reconfirmation() -> None:
    """A discontinuity cannot recover healthy on the next single frame."""
    core = ready_core(max_translation_jump_m=0.2)
    assert core.evaluate(sample(stamp_ns=1_000_000_000, x=0.0)).healthy is False
    assert core.evaluate(sample(stamp_ns=1_100_000_000, x=0.01)).healthy is False
    assert core.evaluate(sample(stamp_ns=1_200_000_000, x=0.02)).healthy is True

    jumped = core.evaluate(sample(stamp_ns=1_300_000_000, x=1.0))
    assert jumped.healthy is False
    assert jumped.reason == "position_jump"
    assert core.evaluate(sample(stamp_ns=1_400_000_000, x=1.01)).healthy is False
    assert core.evaluate(sample(stamp_ns=1_500_000_000, x=1.02)).healthy is True


def test_odom_reset_is_detected_as_output_jump() -> None:
    """A chassis odometry reset cannot silently jump the normalized TF."""
    core = ready_core(max_translation_jump_m=0.2)
    assert core.evaluate(sample(stamp_ns=1_000_000_000)).healthy is False
    assert core.evaluate(sample(stamp_ns=1_100_000_000, x=0.01)).healthy is False
    assert core.evaluate(sample(stamp_ns=1_200_000_000, x=0.02)).healthy is True

    reset_odom = Transform3((10.0, 0.0, 0.0), IDENTITY.rotation)
    outcome = core.evaluate(
        sample(
            stamp_ns=1_300_000_000,
            x=0.03,
            odom_to_base=reset_odom,
        )
    )

    assert outcome.healthy is False
    assert outcome.reason == "map_to_odom_position_jump"
