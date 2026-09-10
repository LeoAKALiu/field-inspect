"""Normalize FAST-LIO odometry into the sole standard ``map -> odom`` TF."""

from __future__ import annotations

import time
from typing import Optional

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import Transform, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from std_msgs.msg import Bool
from tf2_ros import Buffer, TransformBroadcaster, TransformException, TransformListener

from scout_bringup.localization_core import (
    LocalizationConfig,
    LocalizationCore,
    LocalizationOutcome,
    LocalizationSample,
    Transform3,
)
from scout_bringup.node_runner import run_node


def _transform3_from_pose(message: Odometry) -> Transform3:
    position = message.pose.pose.position
    orientation = message.pose.pose.orientation
    return Transform3(
        (position.x, position.y, position.z),
        (orientation.x, orientation.y, orientation.z, orientation.w),
    )


def _transform3_from_message(message: Transform) -> Transform3:
    translation = message.translation
    rotation = message.rotation
    return Transform3(
        (translation.x, translation.y, translation.z),
        (rotation.x, rotation.y, rotation.z, rotation.w),
    )


class LocalizationAdapter(Node):
    """Publish normalized localization only while every prerequisite is healthy."""

    def __init__(self) -> None:
        super().__init__("localization_adapter")
        self.declare_parameter("input_odom_topic", "/Odometry")
        self.declare_parameter("health_topic", "/safety/tf_healthy")
        self.declare_parameter("diagnostics_topic", "/diagnostics")
        self.declare_parameter("input_global_frame", "camera_init")
        self.declare_parameter("body_frame", "body")
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_link")
        self.declare_parameter("pose_timeout_ms", 500)
        self.declare_parameter("tf_lookup_timeout_ms", 50)
        self.declare_parameter("future_tolerance_ms", 50)
        self.declare_parameter("max_translation_jump_m", 0.5)
        self.declare_parameter("max_rotation_jump_rad", 0.35)
        self.declare_parameter("healthy_confirmations", 3)
        self.declare_parameter("extrinsics_verified", False)

        self._map_frame = str(self.get_parameter("map_frame").value)
        self._odom_frame = str(self.get_parameter("odom_frame").value)
        self._base_frame = str(self.get_parameter("base_frame").value)
        self._body_frame = str(self.get_parameter("body_frame").value)
        self._pose_timeout_ms = int(self.get_parameter("pose_timeout_ms").value)
        self._tf_lookup_timeout = Duration(
            nanoseconds=int(self.get_parameter("tf_lookup_timeout_ms").value) * 1_000_000
        )
        config = LocalizationConfig(
            input_global_frame=str(self.get_parameter("input_global_frame").value),
            body_frame=self._body_frame,
            pose_timeout_ms=self._pose_timeout_ms,
            future_tolerance_ms=int(self.get_parameter("future_tolerance_ms").value),
            max_translation_jump_m=float(
                self.get_parameter("max_translation_jump_m").value
            ),
            max_rotation_jump_rad=float(
                self.get_parameter("max_rotation_jump_rad").value
            ),
            healthy_confirmations=int(
                self.get_parameter("healthy_confirmations").value
            ),
            extrinsics_verified=bool(
                self.get_parameter("extrinsics_verified").value
            ),
        )
        self._core = LocalizationCore(config)

        self._health_pub = self.create_publisher(
            Bool,
            str(self.get_parameter("health_topic").value),
            10,
        )
        self._diagnostics_pub = self.create_publisher(
            DiagnosticArray,
            str(self.get_parameter("diagnostics_topic").value),
            10,
        )
        self._tf_buffer = Buffer(cache_time=Duration(seconds=2.0))
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._tf_broadcaster = TransformBroadcaster(self)
        self._last_receive_monotonic_ns: Optional[int] = None
        self._last_stamp_ns: Optional[int] = None
        self._last_outcome = LocalizationOutcome(False, "pose_missing", None, 0)

        self.create_subscription(
            Odometry,
            str(self.get_parameter("input_odom_topic").value),
            self._on_odometry,
            qos_profile_sensor_data,
        )
        self.create_timer(0.1, self._publish_periodic_status)

    def _lookup(
        self,
        target_frame: str,
        source_frame: str,
        stamp: Time,
    ) -> Optional[Transform3]:
        try:
            result = self._tf_buffer.lookup_transform(
                target_frame,
                source_frame,
                stamp,
                timeout=self._tf_lookup_timeout,
            )
        except TransformException:
            return None
        return _transform3_from_message(result.transform)

    def _on_odometry(self, message: Odometry) -> None:
        stamp = Time.from_msg(message.header.stamp)
        base_to_body = self._lookup(self._base_frame, self._body_frame, stamp)
        odom_to_base = self._lookup(self._odom_frame, self._base_frame, stamp)
        now = self.get_clock().now()
        sample = LocalizationSample(
            stamp_ns=stamp.nanoseconds,
            now_ns=now.nanoseconds,
            parent_frame=message.header.frame_id,
            child_frame=message.child_frame_id,
            map_to_body=_transform3_from_pose(message),
            base_to_body=base_to_body,
            odom_to_base=odom_to_base,
        )
        self._last_receive_monotonic_ns = time.monotonic_ns()
        self._last_stamp_ns = stamp.nanoseconds
        self._last_outcome = self._core.evaluate(sample)
        if self._last_outcome.healthy and self._last_outcome.map_to_odom is not None:
            self._broadcast_transform(message, self._last_outcome.map_to_odom)
        self._publish_status(self._last_outcome)

    def _broadcast_transform(self, source: Odometry, transform: Transform3) -> None:
        message = TransformStamped()
        message.header.stamp = source.header.stamp
        message.header.frame_id = self._map_frame
        message.child_frame_id = self._odom_frame
        message.transform.translation.x = transform.translation[0]
        message.transform.translation.y = transform.translation[1]
        message.transform.translation.z = transform.translation[2]
        message.transform.rotation.x = transform.rotation[0]
        message.transform.rotation.y = transform.rotation[1]
        message.transform.rotation.z = transform.rotation[2]
        message.transform.rotation.w = transform.rotation[3]
        self._tf_broadcaster.sendTransform(message)

    def _publish_periodic_status(self) -> None:
        if self._last_receive_monotonic_ns is None:
            outcome = LocalizationOutcome(False, "pose_missing", None, 0)
        else:
            elapsed_ns = time.monotonic_ns() - self._last_receive_monotonic_ns
            if elapsed_ns > self._pose_timeout_ms * 1_000_000:
                if self._last_outcome.reason != "pose_stream_stale":
                    self._core.reset()
                outcome = LocalizationOutcome(False, "pose_stream_stale", None, 0)
                self._last_outcome = outcome
            else:
                outcome = self._last_outcome
        self._publish_status(outcome)

    def _publish_status(self, outcome: LocalizationOutcome) -> None:
        self._health_pub.publish(Bool(data=outcome.healthy))

        status = DiagnosticStatus()
        status.name = "safety/localization_adapter"
        status.hardware_id = "fast_lio"
        if outcome.healthy:
            status.level = DiagnosticStatus.OK
        elif outcome.reason == "warming_up":
            status.level = DiagnosticStatus.WARN
        else:
            status.level = DiagnosticStatus.ERROR
        status.message = outcome.reason
        status.values = [
            KeyValue(key="healthy", value=str(outcome.healthy).lower()),
            KeyValue(key="reason", value=outcome.reason),
            KeyValue(key="confirmations", value=str(outcome.confirmations)),
            KeyValue(key="map_frame", value=self._map_frame),
            KeyValue(key="odom_frame", value=self._odom_frame),
            KeyValue(key="base_frame", value=self._base_frame),
            KeyValue(key="body_frame", value=self._body_frame),
            KeyValue(key="last_pose_stamp_ns", value=str(self._last_stamp_ns or 0)),
        ]
        message = DiagnosticArray()
        message.header.stamp = self.get_clock().now().to_msg()
        message.status = [status]
        self._diagnostics_pub.publish(message)


def main(args=None) -> None:
    """Run the localization adapter node."""
    run_node(
        LocalizationAdapter,
        args=args,
        executor_factory=lambda: MultiThreadedExecutor(num_threads=2),
    )


if __name__ == "__main__":
    main()
