"""End-to-end launch test for point-cloud obstacle stopping."""

from __future__ import annotations

import struct
import time
import unittest

import launch_testing.actions
import pytest
import rclpy
from builtin_interfaces.msg import Time
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from geometry_msgs.msg import Twist
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare
from rclpy.node import Node
from sensor_msgs.msg import Joy, PointCloud2, PointField
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header


@pytest.mark.launch_test
def generate_test_description() -> LaunchDescription:
    """Launch safety arbitration with the collision monitor enabled."""
    launch_file = PathJoinSubstitution(
        [FindPackageShare("scout_bringup"), "launch", "demo.launch.py"]
    )
    return LaunchDescription(
        [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(launch_file),
                launch_arguments={
                    "start_camera": "false",
                    "start_safety": "true",
                    "start_inspection": "false",
                    "start_scout": "false",
                    "start_livox": "false",
                    "start_fast_lio": "false",
                    "start_collision": "true",
                }.items(),
            ),
            launch_testing.actions.ReadyToTest(),
        ]
    )


class TestCollisionPipeline(unittest.TestCase):
    """Exercise clear, obstacle, and recovery states through ROS topics."""

    def setUp(self) -> None:
        rclpy.init()
        self.node = Node("test_collision_pipeline")
        self.command_pub = self.node.create_publisher(Twist, "/cmd_vel_teleop", 10)
        self.joy_pub = self.node.create_publisher(Joy, "/joy", 10)
        self.cloud_pub = self.node.create_publisher(
            PointCloud2,
            "/cloud_registered_body",
            10,
        )
        self.latest_linear = None
        self.safe_command_count = 0
        self.latest_collision_diagnostic = None
        self.collision_diagnostics = []
        self.latest_mux_diagnostic = None
        self.node.create_subscription(Twist, "/cmd_vel_safe", self._on_safe_command, 10)
        self.node.create_subscription(
            DiagnosticArray,
            "/diagnostics",
            self._on_diagnostics,
            10,
        )

    def tearDown(self) -> None:
        self.node.destroy_node()
        rclpy.shutdown()

    def _on_safe_command(self, message: Twist) -> None:
        self.latest_linear = message.linear.x
        self.safe_command_count += 1

    def _on_diagnostics(self, message: DiagnosticArray) -> None:
        for status in message.status:
            if status.name == "safety/collision_monitor":
                self.latest_collision_diagnostic = status
                self.collision_diagnostics.append(status)
            elif status.name == "safety/safety_mux":
                self.latest_mux_diagnostic = status

    @staticmethod
    def _cloud(points: list[tuple[float, float, float]]) -> PointCloud2:
        header = Header(stamp=Time(), frame_id="body")
        return point_cloud2.create_cloud_xyz32(header, points)

    @staticmethod
    def _float64_cloud(points: list[tuple[float, float, float]]) -> PointCloud2:
        header = Header(stamp=Time(), frame_id="body")
        fields = [
            PointField(name="x", offset=0, datatype=PointField.FLOAT64, count=1),
            PointField(name="y", offset=8, datatype=PointField.FLOAT64, count=1),
            PointField(name="z", offset=16, datatype=PointField.FLOAT64, count=1),
        ]
        return point_cloud2.create_cloud(header, fields, points)

    @staticmethod
    def _overlapping_xyz_cloud(
        points: list[tuple[float, float, float]],
    ) -> PointCloud2:
        header = Header(stamp=Time(), frame_id="body")
        cloud = point_cloud2.create_cloud_xyz32(header, points)
        cloud.fields[1].offset = 0
        cloud.fields[2].offset = 0
        return cloud

    @staticmethod
    def _row_padded_cloud() -> PointCloud2:
        header = Header(stamp=Time(), frame_id="body")
        cloud = point_cloud2.create_cloud_xyz32(header, [(1.5, 0.0, 0.0)])
        clear_point = struct.pack("<fff", 1.5, 0.0, 0.0)
        padding_point = struct.pack("<fff", 0.30, 0.0, 0.0)
        row = clear_point + padding_point * 5
        cloud.width = 1
        cloud.height = 2
        cloud.row_step = len(row)
        cloud.data = list(row + row)
        return cloud

    def _wait_for_output(
        self,
        expected_linear: float,
        cloud: PointCloud2,
        *,
        command_linear: float = 0.1,
        timeout_s: float = 5.0,
    ) -> None:
        if abs(expected_linear) > 1e-6:
            self._release_motion_permit(cloud)
        first_new_command = self.safe_command_count + 1
        command = Twist()
        command.linear.x = command_linear
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.command_pub.publish(command)
            self._publish_motion_permit(True)
            self.cloud_pub.publish(cloud)
            rclpy.spin_once(self.node, timeout_sec=0.05)
            if (
                self.safe_command_count >= first_new_command
                and self.latest_linear is not None
                and abs(self.latest_linear - expected_linear) < 1e-6
            ):
                return
        self.fail(
            f"expected /cmd_vel_safe linear.x={expected_linear}, "
            f"last value was {self.latest_linear}"
        )

    def _publish_motion_permit(self, enabled: bool) -> None:
        joy = Joy()
        joy.axes = [0.0, 0.0]
        joy.buttons = [0, 0, 0, 0, 0, int(enabled)]
        self.joy_pub.publish(joy)

    def _release_motion_permit(
        self,
        cloud: PointCloud2,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        """Make every motion assertion start from an explicit released state."""
        command = Twist()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.command_pub.publish(command)
            self._publish_motion_permit(False)
            self.cloud_pub.publish(cloud)
            rclpy.spin_once(self.node, timeout_sec=0.05)
            diagnostic = self.latest_mux_diagnostic
            if diagnostic is not None and diagnostic.message in {
                "hold_to_run_released",
                "motion_rearmed",
            }:
                return
        last_reason = (
            None
            if self.latest_mux_diagnostic is None
            else self.latest_mux_diagnostic.message
        )
        self.fail(f"motion permit release was not accepted; last reason={last_reason!r}")

    def _wait_for_collision_diagnostic(
        self,
        expected_message: str,
        *,
        timeout_s: float = 2.0,
    ) -> DiagnosticStatus:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            for diagnostic in self.collision_diagnostics:
                if expected_message in diagnostic.message:
                    return diagnostic
        last_message = (
            None
            if self.latest_collision_diagnostic is None
            else self.latest_collision_diagnostic.message
        )
        self.fail(
            f"expected collision diagnostic containing {expected_message!r}, "
            f"last message was {last_message!r}"
        )

    def _wait_for_mux_diagnostic(
        self,
        expected_reason: str,
        *,
        timeout_s: float = 2.0,
    ) -> DiagnosticStatus:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            diagnostic = self.latest_mux_diagnostic
            if diagnostic is not None and diagnostic.message == expected_reason:
                return diagnostic
        last_message = (
            None
            if self.latest_mux_diagnostic is None
            else self.latest_mux_diagnostic.message
        )
        self.fail(
            f"expected safety mux diagnostic reason {expected_reason!r}, "
            f"last message was {last_message!r}"
        )

    def _wait_for_invalid_cloud(
        self,
        cloud: PointCloud2,
        expected_diagnostic: str,
        *,
        timeout_s: float = 5.0,
    ) -> DiagnosticStatus:
        """Wait for evidence that this invalid sample caused a fail-closed output."""
        self.latest_linear = None
        self.collision_diagnostics.clear()
        command = Twist()
        command.linear.x = 0.1
        observed_diagnostic = None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.command_pub.publish(command)
            self._publish_motion_permit(True)
            self.cloud_pub.publish(cloud)
            rclpy.spin_once(self.node, timeout_sec=0.05)
            for diagnostic in self.collision_diagnostics:
                if expected_diagnostic in diagnostic.message:
                    observed_diagnostic = diagnostic
                    break
            if (
                observed_diagnostic is not None
                and self.latest_linear is not None
                and abs(self.latest_linear) < 1e-6
            ):
                return observed_diagnostic
        last_message = (
            None
            if self.latest_collision_diagnostic is None
            else self.latest_collision_diagnostic.message
        )
        self.fail(
            f"invalid cloud did not produce diagnostic {expected_diagnostic!r} "
            f"and a fresh zero command; last diagnostic={last_message!r}, "
            f"last linear={self.latest_linear!r}"
        )

    def _assert_invalid_cloud_fails_closed(
        self,
        invalid_cloud: PointCloud2,
        expected_diagnostic: str,
    ) -> None:
        clear_cloud = self._cloud([(1.5, 0.0, 0.0)] * 6)
        self._wait_for_output(0.1, clear_cloud)
        diagnostic = self._wait_for_invalid_cloud(
            invalid_cloud,
            expected_diagnostic,
        )
        self.assertEqual(diagnostic.level, DiagnosticStatus.ERROR)
        self.assertIn(expected_diagnostic, diagnostic.message)

    def test_obstacle_stops_and_clear_frames_resume(self) -> None:
        """An obstacle must stop teleop and three clear clouds must permit it again."""
        clear_cloud = self._cloud([(1.5, 0.0, 0.0)] * 6)
        obstacle_cloud = self._cloud(
            [
                (0.30, 0.00, 0.00),
                (0.32, 0.05, 0.00),
                (0.34, -0.05, 0.00),
                (0.36, 0.10, 0.00),
                (0.38, -0.10, 0.00),
            ]
        )

        self._wait_for_output(0.1, clear_cloud)
        self._wait_for_output(0.0, obstacle_cloud)
        self._wait_for_output(0.1, clear_cloud)

    def test_well_formed_empty_cloud_counts_as_a_clear_frame(self) -> None:
        """A valid zero-point cloud may recover only through clear confirmations."""
        clear_cloud = self._cloud([(1.5, 0.0, 0.0)] * 6)
        obstacle_cloud = self._cloud([(0.30, 0.0, 0.0)] * 6)
        empty_cloud = self._cloud([])

        self._wait_for_output(0.1, clear_cloud)
        self._wait_for_output(0.0, obstacle_cloud)
        self._wait_for_output(0.1, empty_cloud)

        diagnostic = self._wait_for_collision_diagnostic("stop zone clear")
        self.assertEqual(diagnostic.level, DiagnosticStatus.OK)

    def test_obstacle_stop_reason_is_published_for_recording(self) -> None:
        """The recorded diagnostics stream must explain an obstacle safety stop."""
        clear_cloud = self._cloud([(1.5, 0.0, 0.0)] * 6)
        obstacle_cloud = self._cloud([(0.30, 0.0, 0.0)] * 6)

        self._wait_for_output(0.1, clear_cloud)
        self.latest_mux_diagnostic = None
        self._wait_for_output(0.0, obstacle_cloud)

        diagnostic = self._wait_for_mux_diagnostic("obstacle_stop")
        self.assertEqual(diagnostic.level, DiagnosticStatus.WARN)

    def test_clear_cloud_still_applies_existing_velocity_limit(self) -> None:
        """A clear stop zone must not bypass the safety mux velocity limits."""
        clear_cloud = self._cloud([(1.5, 0.0, 0.0)] * 6)

        self._wait_for_output(0.2, clear_cloud, command_linear=1.0)

    def test_invalid_point_field_layout_fails_closed_and_reports_diagnostic(self) -> None:
        """Non-FLOAT32 XYZ fields must never be interpreted as a clear stop zone."""
        invalid_cloud = self._float64_cloud([(0.30, 0.0, 0.0)] * 6)

        self._assert_invalid_cloud_fails_closed(invalid_cloud, "field layout")

    def test_wrong_frame_fails_closed_and_does_not_refresh_cloud_heartbeat(self) -> None:
        """A stream in the wrong frame must age into a monitor-stale safety stop."""
        clear_cloud = self._cloud([(1.5, 0.0, 0.0)] * 6)
        wrong_frame_cloud = self._cloud([(1.5, 0.0, 0.0)] * 6)
        wrong_frame_cloud.header.frame_id = "map"

        self._wait_for_output(0.1, clear_cloud)
        diagnostic = self._wait_for_invalid_cloud(
            wrong_frame_cloud,
            "unexpected frame",
        )
        self.assertEqual(diagnostic.level, DiagnosticStatus.ERROR)

        deadline = time.monotonic() + 0.8
        command = Twist()
        command.linear.x = 0.1
        while time.monotonic() < deadline:
            self.command_pub.publish(command)
            self.cloud_pub.publish(wrong_frame_cloud)
            rclpy.spin_once(self.node, timeout_sec=0.05)

        diagnostic = self.latest_collision_diagnostic
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic.message, "point cloud stale or missing")
        self.assertEqual(diagnostic.level, DiagnosticStatus.ERROR)

    def test_big_endian_point_cloud_fails_closed_and_reports_diagnostic(self) -> None:
        """The little-endian Jetson must reject unsupported big-endian point data."""
        unsupported_cloud = self._cloud([(1.5, 0.0, 0.0)] * 6)
        unsupported_cloud.is_bigendian = True

        self._assert_invalid_cloud_fails_closed(unsupported_cloud, "big-endian")

    def test_overlapping_xyz_fields_fail_closed_and_report_diagnostic(self) -> None:
        """Corrupt XYZ offsets must not be interpreted as an obstacle-free cloud."""
        invalid_cloud = self._overlapping_xyz_cloud([(1.5, 0.0, 0.0)] * 6)

        self._assert_invalid_cloud_fails_closed(invalid_cloud, "overlap")

    def test_clear_confirmation_wait_reports_the_latched_stop_reason(self) -> None:
        """A clear sample before recovery must not be diagnosed as a current obstacle."""
        clear_cloud = self._cloud([(1.5, 0.0, 0.0)] * 6)
        obstacle_cloud = self._cloud([(0.30, 0.0, 0.0)] * 6)

        self._wait_for_output(0.1, clear_cloud)
        self._wait_for_output(0.0, obstacle_cloud)
        self.latest_collision_diagnostic = None
        self.cloud_pub.publish(clear_cloud)

        diagnostic = self._wait_for_collision_diagnostic(
            "waiting for consecutive clear point clouds"
        )
        self.assertEqual(diagnostic.level, DiagnosticStatus.WARN)

    def test_row_padding_is_rejected_instead_of_parsed_as_points(self) -> None:
        """Unsupported row padding must fail closed without counting padding bytes."""
        padded_cloud = self._row_padded_cloud()

        self._assert_invalid_cloud_fails_closed(padded_cloud, "row padding")
