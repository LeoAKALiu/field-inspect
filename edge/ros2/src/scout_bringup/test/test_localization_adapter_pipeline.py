"""ROS integration test for the FAST-LIO localization adapter."""

from __future__ import annotations

import time
import json
import tempfile
import unittest
from pathlib import Path

import launch_testing.actions
import pytest
import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import TransformStamped
from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image
from inspection_pipeline.station_capture import StationCapture
from rclpy.duration import Duration
from rclpy.node import Node as RclpyNode
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from std_msgs.msg import Bool
from tf2_ros import Buffer, TransformBroadcaster, TransformListener


@pytest.mark.launch_test
def generate_test_description() -> LaunchDescription:
    """Launch a verified adapter plus the static base-to-body extrinsic."""
    config_file = PathJoinSubstitution(
        [FindPackageShare("scout_bringup"), "config", "system.yaml"]
    )
    return LaunchDescription(
        [
            Node(
                package="tf2_ros",
                executable="static_transform_publisher",
                name="test_body_static_tf",
                additional_env={"ROS_DOMAIN_ID": "186"},
                arguments=[
                    "--x",
                    "1.0",
                    "--y",
                    "0",
                    "--z",
                    "0",
                    "--roll",
                    "0",
                    "--pitch",
                    "0",
                    "--yaw",
                    "1.5707963267948966",
                    "--frame-id",
                    "base_link",
                    "--child-frame-id",
                    "body",
                ],
            ),
            Node(
                package="tf2_ros", executable="static_transform_publisher",
                name="test_parent_static_tf",
                additional_env={"ROS_DOMAIN_ID": "186"},
                arguments=["--x", "10", "--y", "-2", "--z", "0", "--yaw", "1.5707963267948966",
                           "--frame-id", "map", "--child-frame-id", "camera_init"],
            ),
            Node(
                package="scout_bringup",
                executable="localization_adapter",
                name="localization_adapter",
                additional_env={"ROS_DOMAIN_ID": "186"},
                parameters=[
                    config_file,
                    {
                        "extrinsics_verified": True,
                        "pose_timeout_ms": 300,
                        "tf_lookup_timeout_ms": 100,
                        "healthy_confirmations": 3,
                    },
                ],
                output="screen",
            ),
            launch_testing.actions.ReadyToTest(),
        ]
    )


class TestLocalizationAdapterPipeline(unittest.TestCase):
    """Exercise TF composition, health recovery, jumps, and stream timeout."""

    def setUp(self) -> None:
        rclpy.init(domain_id=186)
        self.node = RclpyNode("test_localization_adapter_pipeline")
        self.odom_pub = self.node.create_publisher(
            Odometry,
            "/Odometry",
            qos_profile_sensor_data,
        )
        self.tf_broadcaster = TransformBroadcaster(self.node)
        self.tf_buffer = Buffer(cache_time=Duration(seconds=2.0))
        self.tf_listener = TransformListener(self.tf_buffer, self.node)
        self.latest_health = None
        self.latest_reason = None
        self.published_x_by_stamp = {}
        self.capture = None
        self.motion_pub = self.node.create_publisher(Odometry, "/odom", 10)
        self.image_pub = self.node.create_publisher(Image, "/camera/image_raw", 10)
        self.node.create_subscription(Odometry, "/localization/odometry", self._on_normalized, 10)
        self.node.create_subscription(Odometry, "/odom", self._on_motion, 10)
        self.node.create_subscription(Image, "/camera/image_raw", self._on_image, 10)
        self.node.create_subscription(Bool, "/safety/tf_healthy", self._on_health, 10)
        self.node.create_subscription(
            DiagnosticArray,
            "/diagnostics",
            self._on_diagnostics,
            10,
        )

    def tearDown(self) -> None:
        self.node.destroy_node()
        rclpy.shutdown()

    def _on_health(self, message: Bool) -> None:
        self.latest_health = message.data
        if self.capture is not None:
            self.capture.localization_health(message.data)

    def _on_normalized(self, message):
        if self.capture is not None:
            self.capture.odometry(message, "map", "base_link", self.node.get_clock().now().nanoseconds)

    def _on_motion(self, message):
        if self.capture is not None:
            self.capture.measured_motion(message, "odom", "base_link", self.node.get_clock().now().nanoseconds)

    def _on_image(self, message):
        if self.capture is not None:
            self.capture.image(message, self.node.get_clock().now().nanoseconds)

    def _on_diagnostics(self, message: DiagnosticArray) -> None:
        for status in message.status:
            if status.name == "safety/localization_adapter":
                self.latest_reason = status.message

    def _publish_pose(self, x: float, *, valid_covariance=True, with_tf=True) -> None:
        stamp = self.node.get_clock().now().to_msg()
        self.published_x_by_stamp[(stamp.sec, stamp.nanosec)] = x
        odom_to_base = TransformStamped()
        odom_to_base.header.stamp = stamp
        odom_to_base.header.frame_id = "odom"
        odom_to_base.child_frame_id = "base_link"
        odom_to_base.transform.translation.x = 2.0
        odom_to_base.transform.rotation.w = 1.0
        if with_tf:
            self.tf_broadcaster.sendTransform(odom_to_base)
        time.sleep(0.03)

        pose = Odometry()
        pose.header.stamp = stamp
        pose.header.frame_id = "camera_init"
        pose.child_frame_id = "body"
        pose.pose.pose.position.x = x
        pose.pose.pose.orientation.w = 1.0
        pose.pose.covariance = [0.001 if i % 7 == 0 else 0. for i in range(36)]
        if not valid_covariance:
            pose.pose.covariance[35] = -1.
        motion = Odometry()
        motion.header.stamp = stamp
        motion.header.frame_id = "odom"
        motion.child_frame_id = "base_link"
        self.motion_pub.publish(motion)
        self.odom_pub.publish(pose)
        image = Image()
        image.header.stamp = stamp
        image.header.frame_id = "camera_optical_frame"
        image.height = image.width = image.step = 2
        image.encoding = "mono8"
        image.data = bytes(4)
        self.image_pub.publish(image)

    def _wait_for_state(
        self,
        health: bool,
        reason: str,
        *,
        publish_x: float | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if publish_x is not None:
                self._publish_pose(publish_x)
                publish_x += 0.01
            rclpy.spin_once(self.node, timeout_sec=0.05)
            if self.latest_health is health and self.latest_reason == reason:
                return
        self.fail(
            f"expected health={health}, reason={reason!r}; "
            f"last health={self.latest_health}, reason={self.latest_reason!r}"
        )

    def _wait_for_map_to_odom(self, timeout_s: float = 2.0) -> TransformStamped:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            if self.tf_buffer.can_transform("map", "odom", Time()):
                return self.tf_buffer.lookup_transform("map", "odom", Time())
        self.fail("map -> odom was not received after localization became healthy")

    def test_normalizes_tf_and_fails_closed_on_jump_and_timeout(self) -> None:
        """Only confirmed continuous poses may produce healthy map-to-odom TF."""
        self._wait_for_state(True, "healthy", publish_x=10.0)

        transform = self._wait_for_map_to_odom()
        stamp_key = (transform.header.stamp.sec, transform.header.stamp.nanosec)
        self.assertIn(stamp_key, self.published_x_by_stamp)
        self.assertAlmostEqual(
            transform.transform.translation.x,
            7.0,
            places=6,
        )
        self.assertAlmostEqual(transform.transform.translation.y,
                               self.published_x_by_stamp[stamp_key] - 2.0, places=6)

        self._publish_pose(10.03, valid_covariance=False)
        self._wait_for_state(False, "odometry_invalid:covariance_invalid")
        self._wait_for_state(True, "healthy", publish_x=10.04)
        self._publish_pose(10.1, with_tf=False)
        self._wait_for_state(False, "odom_transform_missing")
        self._wait_for_state(True, "healthy", publish_x=10.11)

        self._publish_pose(100.0)
        self._wait_for_state(False, "position_jump")
        self._wait_for_state(False, "pose_stream_stale", timeout_s=2.0)

    def test_normalized_messages_reach_capture_metadata(self):
        """Only synthetic publishers and adapter are launched; no control endpoints."""
        with tempfile.TemporaryDirectory() as directory:
            self.capture = StationCapture(dwell_seconds=.1)
            self.capture.begin(Path(directory), "synthetic-run", "execution", "station")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not self.capture.ready:
                self._publish_pose(10.)
                rclpy.spin_once(self.node, timeout_sec=.02)
            self.assertTrue(self.capture.ready, self.capture.reason)
            source = json.loads((self.capture.directory / "source.json").read_text())
            self.assertEqual(source["schema_version"], "1.3")
            self.assertEqual(source["pose"]["frame_id"], "map")
            self.assertEqual(source["pose"]["child_frame_id"], "base_link")
            self.assertAlmostEqual(source["pose"]["position"]["x"], 9.)
            self.assertAlmostEqual(source["pose"]["position"]["y"], 8.)
            self.assertEqual(len(source["pose"]["covariance_6x6"]), 36)
            self.assertTrue(source["localization_healthy"])
            self.capture = None
