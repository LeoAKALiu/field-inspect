"""End-to-end launch test for hold-to-run and motion re-arming."""

from __future__ import annotations

import time
import unittest

import launch_testing.actions
import pytest
import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import Twist
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare
from rclpy.node import Node as RclpyNode
from sensor_msgs.msg import Joy
from std_msgs.msg import Bool


@pytest.mark.launch_test
def generate_test_description() -> LaunchDescription:
    """Launch only the safety boundary under test."""
    config_file = PathJoinSubstitution(
        [FindPackageShare("scout_bringup"), "config", "system.yaml"]
    )
    return LaunchDescription(
        [
            Node(
                package="safety_mux",
                executable="safety_mux_node",
                name="safety_mux",
                parameters=[config_file],
                output="screen",
            ),
            launch_testing.actions.ReadyToTest(),
        ]
    )


class TestHoldToRunPipeline(unittest.TestCase):
    """Verify the operator permit at the final safe-command boundary."""

    def setUp(self) -> None:
        rclpy.init()
        self.node = RclpyNode("test_hold_to_run_pipeline")
        self.command_pub = self.node.create_publisher(Twist, "/cmd_vel_teleop", 10)
        self.joy_pub = self.node.create_publisher(Joy, "/joy", 10)
        self.estop_pub = self.node.create_publisher(
            Bool, "/safety/emergency_stop", 10
        )
        self.chassis_pub = self.node.create_publisher(
            Bool, "/safety/chassis_fault", 10
        )
        self.lidar_pub = self.node.create_publisher(
            Bool, "/safety/lidar_healthy", 10
        )
        self.tf_pub = self.node.create_publisher(Bool, "/safety/tf_healthy", 10)
        self.latest_linear = None
        self.latest_reason = None
        self.node.create_subscription(Twist, "/cmd_vel_safe", self._on_command, 10)
        self.node.create_subscription(
            DiagnosticArray, "/diagnostics", self._on_diagnostics, 10
        )

    def tearDown(self) -> None:
        self.node.destroy_node()
        rclpy.shutdown()

    def _on_command(self, message: Twist) -> None:
        self.latest_linear = message.linear.x

    def _on_diagnostics(self, message: DiagnosticArray) -> None:
        for status in message.status:
            if status.name == "safety/safety_mux":
                self.latest_reason = status.message

    def _publish_inputs(
        self,
        permit: bool | None,
        estop: bool,
        chassis_fault: bool,
        publish_command: bool,
    ) -> None:
        command = Twist()
        command.linear.x = 0.1
        if publish_command:
            self.command_pub.publish(command)
        if permit is not None:
            joy = Joy()
            joy.axes = [0.0, 0.0]
            joy.buttons = [0, 0, 0, 0, 0, int(permit)]
            self.joy_pub.publish(joy)
        self.estop_pub.publish(Bool(data=estop))
        self.chassis_pub.publish(Bool(data=chassis_fault))
        self.lidar_pub.publish(Bool(data=True))
        self.tf_pub.publish(Bool(data=True))

    def _wait_for(
        self,
        expected_linear: float,
        expected_reason: str,
        *,
        permit: bool | None,
        estop: bool = False,
        chassis_fault: bool = False,
        publish_command: bool = True,
        timeout_s: float = 5.0,
    ) -> None:
        self.latest_linear = None
        self.latest_reason = None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._publish_inputs(
                permit,
                estop,
                chassis_fault,
                publish_command,
            )
            rclpy.spin_once(self.node, timeout_sec=0.05)
            if (
                self.latest_linear is not None
                and abs(self.latest_linear - expected_linear) < 1e-6
                and self.latest_reason == expected_reason
            ):
                return
        self.fail(
            "expected /cmd_vel_safe "
            f"linear.x={expected_linear}, reason={expected_reason!r}; "
            f"last values were {self.latest_linear}, {self.latest_reason!r}"
        )

    def test_hold_to_run_and_release_to_rearm(self) -> None:
        """Missing/released permit stops and a safety trip requires a release."""
        self._wait_for(0.0, "hold_to_run_released", permit=False)
        self._wait_for(
            0.0,
            "hold_to_run_missing_or_stale",
            permit=None,
        )
        self._wait_for(0.1, "teleop", permit=True)
        self._wait_for(0.0, "emergency_stop", permit=True, estop=True)
        self._wait_for(0.0, "motion_rearm_required", permit=True)
        self._wait_for(0.0, "motion_rearmed", permit=False)
        self._wait_for(
            0.0,
            "command_timeout",
            permit=True,
            publish_command=False,
        )
        self._wait_for(0.1, "teleop", permit=True)
        self._wait_for(0.0, "hold_to_run_released", permit=False)

        self._wait_for(0.1, "teleop", permit=True)
        stale_deadline = time.monotonic() + 0.7
        while time.monotonic() < stale_deadline:
            self._publish_inputs(None, False, False, True)
            rclpy.spin_once(self.node, timeout_sec=0.05)
        self._wait_for(
            0.0,
            "chassis_fault",
            permit=None,
            chassis_fault=True,
        )
        self._wait_for(0.0, "motion_rearm_required", permit=True)
        self._wait_for(0.0, "motion_rearmed", permit=False)
