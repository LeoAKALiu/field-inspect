"""Tests for scene-aligned replay trajectory generation."""

from __future__ import annotations

import math
from copy import deepcopy

import pytest

from inspection_pipeline.trajectory import (
    PoseSample,
    RigidTransform,
    SceneAlignment,
    SpeedSample,
    TrajectoryError,
    build_trajectory,
    validate_trajectory_document,
)

IDENTITY = RigidTransform((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def alignment() -> SceneAlignment:
    """Rotate ROS Z-up into the twin's Y-up coordinates."""
    half_angle = -math.pi / 4
    return SceneAlignment(
        alignment_id="synthetic-alignment-v1",
        source_kind="synthetic_contract_fixture",
        input_frame="map",
        output_coordinate_system="scene_local_yup",
        transform=RigidTransform(
            (10.0, 20.0, 30.0),
            (math.sin(half_angle), 0.0, 0.0, math.cos(half_angle)),
        ),
        source_sha256="a" * 64,
    )


def pose(stamp_ns: int, x: float, y: float, z: float) -> PoseSample:
    """Return one valid camera_init-to-body sample."""
    return PoseSample(
        stamp_ns=stamp_ns,
        parent_frame="camera_init",
        child_frame="body",
        map_to_body=RigidTransform((x, y, z), IDENTITY.rotation),
    )


def test_builds_scene_y_up_base_trajectory_with_matched_speed() -> None:
    """Alignment, body extrinsic, heading, timestamps, and speed are deterministic."""
    base_to_body = RigidTransform((1.0, 0.0, 0.0), IDENTITY.rotation)
    stamps = (1_787_472_000_000_000_000, 1_787_472_001_000_000_000)

    result = build_trajectory(
        run_id="run-001",
        alignment=alignment(),
        poses=[pose(stamps[0], 2.0, 3.0, 4.0), pose(stamps[1], 3.0, 3.0, 4.0)],
        base_to_body=base_to_body,
        input_global_frame="camera_init",
        body_frame="body",
        speed_samples=[SpeedSample(stamps[0] + 20_000_000, 0.2)],
        max_speed_skew_ms=100,
    )

    assert result["coordinate_system"] == "scene_local_yup"
    assert result["points"][0]["position"] == pytest.approx(
        {"x": 11.0, "y": 24.0, "z": 27.0}
    )
    assert result["points"][0]["heading_deg"] == pytest.approx(0.0)
    assert result["points"][0]["speed_mps"] == pytest.approx(0.2)
    assert result["points"][1]["speed_mps"] is None
    assert result["points"][0]["timestamp"].endswith("Z")


@pytest.mark.parametrize(
    ("broken_pose", "message"),
    [
        (pose(1_787_472_000_000_000_000, 0.0, 0.0, 0.0), "parent frame"),
        (pose(1_787_472_000_000_000_000, 0.0, 0.0, 0.0), "child frame"),
    ],
)
def test_rejects_frame_drift(broken_pose: PoseSample, message: str) -> None:
    """Recorded FAST-LIO frames must match the frozen run configuration."""
    kwargs = {
        "input_global_frame": "map"
        if message == "parent frame"
        else "camera_init",
        "body_frame": "base_link" if message == "child frame" else "body",
    }
    with pytest.raises(TrajectoryError, match=message):
        build_trajectory(
            run_id="run-001",
            alignment=alignment(),
            poses=[broken_pose],
            base_to_body=IDENTITY,
            **kwargs,
        )


def test_rejects_non_monotonic_pose_timestamps() -> None:
    """A reset sensor clock cannot become a plausible playback timeline."""
    stamp = 1_787_472_000_000_000_000
    with pytest.raises(TrajectoryError, match="strictly increasing"):
        build_trajectory(
            run_id="run-001",
            alignment=alignment(),
            poses=[pose(stamp, 0.0, 0.0, 0.0), pose(stamp, 1.0, 0.0, 0.0)],
            base_to_body=IDENTITY,
            input_global_frame="camera_init",
            body_frame="body",
        )


def test_speed_matching_requires_an_explicit_skew_threshold() -> None:
    """Recorded velocity remains null until its fusion tolerance is accepted."""
    stamp = 1_787_472_000_000_000_000
    result = build_trajectory(
        run_id="run-001",
        alignment=alignment(),
        poses=[pose(stamp, 0.0, 0.0, 0.0)],
        base_to_body=IDENTITY,
        input_global_frame="camera_init",
        body_frame="body",
        speed_samples=[SpeedSample(stamp, 0.2)],
    )

    assert result["points"][0]["speed_mps"] is None


def test_trajectory_contract_rejects_nan_and_sequence_gaps() -> None:
    """Python's permissive JSON NaN and non-contiguous seq values are forbidden."""
    stamp = 1_787_472_000_000_000_000
    document = build_trajectory(
        run_id="run-001",
        alignment=alignment(),
        poses=[pose(stamp, 0.0, 0.0, 0.0)],
        base_to_body=IDENTITY,
        input_global_frame="camera_init",
        body_frame="body",
    )
    broken = deepcopy(document)
    broken["points"][0]["position"]["x"] = float("nan")
    with pytest.raises(TrajectoryError, match="finite numbers"):
        validate_trajectory_document(broken)

    broken = deepcopy(document)
    broken["points"][0]["seq"] = 2
    with pytest.raises(TrajectoryError, match="contiguous"):
        validate_trajectory_document(broken)
