"""Measured stop gate and durable station image capture; never commands motion."""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path


class StationCapture:
    def __init__(self, *, dwell_seconds=2.0, timeout_seconds=30.0):
        self.dwell_ns = int(dwell_seconds * 1e9)
        self.timeout_ns = int(timeout_seconds * 1e9)
        self.key = None
        self.ready = False
        self.reason = "not_at_station"
        self.stable_since = 0
        self.last_odom = 0
        self.last_pose = None
        self.last_stamp = -1
        self.anchor = None
        self.started = 0
        self.attempt = 0
        self.directory = None
        self.failed = False

    def begin(self, run_dir: Path, run_id: str, execution_id: str, station_id: str):
        key = (run_id, execution_id, station_id)
        if self.key == key:
            return
        self.key = key
        self.ready = self.failed = False
        self.stable_since = self.last_odom = 0
        self.last_pose = None
        self.last_stamp = -1
        self.anchor = None
        self.started = time.monotonic_ns()
        self.reason = "waiting_for_measured_stop"
        identity = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]
        root = run_dir / "artifacts/station-captures" / identity
        root.mkdir(parents=True, exist_ok=True)
        # Allocate immutable attempts exclusively, including across process restarts.
        self.attempt = 1
        while True:
            self.directory = root / str(self.attempt)
            try:
                self.directory.mkdir()
                break
            except FileExistsError:
                self.attempt += 1
        self._record("started", None)

    def _record(self, state, image_sha):
        if self.directory is None:
            return
        document = {"run_id": self.key[0], "execution_id": self.key[1], "station_id": self.key[2],
            "attempt_seq": self.attempt, "state": state, "reason": self.reason,
            "recorded_at_ns": time.time_ns(), "image_sha256": image_sha,
            "image_path": "frame.png" if image_sha else None,
            "measured_stop": {"linear_limit_mps": 0.02, "angular_limit_radps": 0.02,
                "dwell_seconds": self.dwell_ns / 1e9, "position_drift_limit_m": 0.03,
                "orientation_drift_limit_rad": 0.02, "position_variance_limit_m2": 0.04}}
        path = self.directory / f"{state}.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        descriptor = os.open(self.directory, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)

    def fail(self, reason):
        if self.key is not None and not self.failed and not self.ready:
            self.reason = reason
            self._record("failed", None)
            self.failed = True

    def leave(self):
        self.fail("station_interrupted_before_capture")
        self.key = None
        self.ready = False

    def odometry(self, message, expected_frame):
        if self.key is None or self.failed or self.ready:
            return
        now = time.monotonic_ns()
        t, pose = message.twist.twist, message.pose.pose
        values = [t.linear.x, t.linear.y, t.linear.z, t.angular.x, t.angular.y, t.angular.z,
                  pose.position.x, pose.position.y, pose.position.z,
                  pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w]
        stamp = message.header.stamp.sec*1_000_000_000 + message.header.stamp.nanosec
        covariance = [message.pose.covariance[i] for i in (0, 7, 14)]
        valid = (message.header.frame_id == expected_frame and stamp > self.last_stamp
            and all(math.isfinite(v) for v in values + covariance)
            and all(0 <= v <= 0.04 for v in covariance)
            and math.sqrt(sum(v*v for v in values[:3])) <= 0.02
            and math.sqrt(sum(v*v for v in values[3:6])) <= 0.02)
        self.last_stamp = stamp
        if self.last_odom and now-self.last_odom > 500_000_000:
            self.stable_since = 0; self.anchor = None
        self.last_odom = now
        q = values[9:13]
        norm = math.sqrt(sum(v*v for v in q))
        valid = valid and abs(norm-1) < 0.01
        if valid and self.anchor is not None:
            distance = math.sqrt(sum((a-b)**2 for a,b in zip(values[6:9], self.anchor[:3])))
            angle = 2*math.acos(min(1.0, abs(sum(a*b for a,b in zip(q,self.anchor[3:])))))
            valid = distance <= 0.03 and angle <= 0.02
        if valid:
            self.last_pose = {"frame_id": message.header.frame_id, "child_frame_id": message.child_frame_id,
                "position": dict(zip(("x", "y", "z"), values[6:9])),
                "orientation_xyzw": q, "position_variance_m2": covariance}
        else:
            self.last_pose = None
        if not valid:
            self.stable_since = 0; self.anchor = None
            self.reason = "measured_motion_or_localization_unstable"
        elif self.stable_since == 0:
            self.stable_since = now; self.anchor = values[6:13]
            self.reason = "waiting_for_stability_dwell"

    def image(self, message):
        now = time.monotonic_ns()
        if self.key is None or self.ready or self.failed:
            return
        if now-self.started > self.timeout_ns:
            self.fail("station_capture_timeout"); return
        if not self.stable_since or now-self.stable_since < self.dwell_ns or now-self.last_odom > 500_000_000:
            return
        stamp = message.header.stamp.sec*1_000_000_000 + message.header.stamp.nanosec
        if abs(stamp-self.last_stamp) > 500_000_000:
            self.reason = "image_pose_timestamp_mismatch"; return
        import cv2
        import numpy as np
        channels = {"rgb8": 3, "bgr8": 3, "mono8": 1}.get(message.encoding)
        try:
            if channels is None: raise ValueError("unsupported camera encoding")
            frame = np.frombuffer(bytes(message.data), np.uint8).reshape(message.height, message.step)
            frame = frame[:, :message.width*channels].reshape(message.height, message.width, channels)
            if message.encoding == "rgb8": frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, encoded = cv2.imencode(".png", frame)
            if not ok: raise ValueError("image encoder failed")
            payload = encoded.tobytes()
            with (self.directory / "frame.png").open("xb") as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            with (self.directory / "source.json").open("x", encoding="utf-8") as stream:
                json.dump({"schema_version": "1.1", "image_header_stamp_ns": stamp, "frame_id": message.header.frame_id,
                           "encoding": message.encoding, "pose_header_stamp_ns": self.last_stamp,
                           "pose": self.last_pose, "pose_method": "latest_measured_odometry",
                           "image_pose_gap_ns": abs(stamp-self.last_stamp)}, stream)
                stream.flush(); os.fsync(stream.fileno())
            self.reason = "evidence_persisted_waiting_for_operator"
            self._record("captured", hashlib.sha256(payload).hexdigest())
            self.ready = True
        except (OSError, ValueError, cv2.error) as exc:
            self.fail(f"capture_failed:{type(exc).__name__}")

    def tick(self):
        if self.key is not None and time.monotonic_ns()-self.started > self.timeout_ns:
            self.fail("station_capture_timeout")
