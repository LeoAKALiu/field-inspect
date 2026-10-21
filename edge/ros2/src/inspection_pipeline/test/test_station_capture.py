"""Capture rejects inconsistent or stale inputs before writing image evidence."""
from copy import deepcopy
from types import SimpleNamespace as NS

import numpy as np
import pytest

from inspection_pipeline.station_capture import StationCapture


def odometry(stamp=10_000_000_000, parent="map", child="base_link"):
    def xyz():
        return NS(x=0., y=0., z=0.)
    return NS(header=NS(frame_id=parent, stamp=NS(sec=stamp//10**9, nanosec=stamp % 10**9)),
              child_frame_id=child,
              pose=NS(pose=NS(position=xyz(), orientation=NS(x=0., y=0., z=0., w=1.)),
                      covariance=(np.eye(6)*.001).ravel().tolist()),
              twist=NS(twist=NS(linear=xyz(), angular=xyz()), covariance=[0.]*36))


def ready(tmp_path, monkeypatch):
    monotonic = [1_000_000_000]
    monkeypatch.setattr("inspection_pipeline.station_capture.time.monotonic_ns", lambda: monotonic[0])
    capture = StationCapture(dwell_seconds=.1)
    capture.begin(tmp_path, "run", "execution", "station")
    capture.localization_health(True)
    capture.measured_motion(odometry(parent="odom"), "odom", "base_link", 10_000_000_000)
    capture.odometry(odometry(), "map", "base_link", 10_000_000_000)
    return capture, monotonic


@pytest.mark.parametrize("failure,reason", [
    ("child", "capture_pose_frame_mismatch"),
    ("time", "capture_pose_timestamp_invalid_or_stale"),
    ("covariance", "capture_covariance_invalid"),
    ("motion", "vehicle_not_stopped"),
    ("health", "localization_unhealthy"),
])
def test_invalid_input_resets_dwell(tmp_path, monkeypatch, failure, reason):
    capture, _ = ready(tmp_path, monkeypatch)
    pose = odometry(10_100_000_000)
    if failure == "child":
        pose.child_frame_id = "body"
    elif failure == "time":
        pose.header.stamp.sec = 1
    elif failure == "covariance":
        pose.pose.covariance[1] = 1
    elif failure == "motion":
        motion = odometry(10_100_000_000, "odom")
        motion.twist.twist.linear.x = .1
        capture.measured_motion(motion, "odom", "base_link", 10_100_000_000)
    else:
        capture.localization_health(False)
    if failure not in {"motion", "health"}:
        capture.odometry(pose, "map", "base_link", 10_100_000_000)
    assert capture.reason == reason
    assert capture.stable_since == 0 and capture.last_pose is None
    assert not capture.ready
    assert not list(tmp_path.rglob("frame.png"))


def test_stream_timeout_and_recovery_require_new_dwell(tmp_path, monkeypatch):
    capture, clock = ready(tmp_path, monkeypatch)
    clock[0] += 600_000_000
    capture.tick()
    assert capture.stable_since == 0
    assert not capture.ready
    capture.localization_health(True)
    capture.measured_motion(odometry(10_600_000_000, "odom"), "odom", "base_link", 10_600_000_000)
    capture.odometry(odometry(10_600_000_000), "map", "base_link", 10_600_000_000)
    assert capture.stable_since == clock[0]
    assert capture.reason == "waiting_for_stability_dwell"


def test_health_gap_resets_dwell_and_does_not_rewrite_failed_attempt(tmp_path, monkeypatch):
    capture, clock = ready(tmp_path, monkeypatch)
    clock[0] += 600_000_000
    capture.localization_health(True)
    assert capture.stable_since == 0
    assert capture.reason == "localization_health_stream_stale"
    capture.fail("operator_skipped_capture")
    capture.localization_health(False)
    assert capture.reason == "operator_skipped_capture"


def test_normalized_metadata_is_written_without_modifying_input(tmp_path, monkeypatch):
    import json
    capture, clock = ready(tmp_path, monkeypatch)
    pose = odometry(10_200_000_000)
    before = deepcopy(pose)
    clock[0] += 200_000_000
    capture.localization_health(True)
    capture.measured_motion(odometry(10_200_000_000, "odom"), "odom", "base_link", 10_200_000_000)
    capture.odometry(pose, "map", "base_link", 10_200_000_000)
    capture.image(NS(header=pose.header, encoding="mono8", height=2, width=2,
                     step=2, data=bytes(4)), 10_200_000_000)
    assert capture.ready and pose == before
    source = json.loads((capture.directory / "source.json").read_text())
    assert source["schema_version"] == "1.3"
    assert source["pose_method"] == "latest_normalized_odometry"
    assert source["pose"]["child_frame_id"] == "base_link"
    assert len(source["pose"]["covariance_6x6"]) == 36
    assert source["measured_motion"]["header_stamp_ns"] == 10_200_000_000
