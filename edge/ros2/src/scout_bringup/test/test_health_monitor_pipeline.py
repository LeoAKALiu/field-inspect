"""Launch tests for the real chassis and emergency-stop health gates."""

from __future__ import annotations

import time
import unittest

import launch_testing.actions
import pytest
import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from nav_msgs.msg import Odometry
from rclpy.node import Node as RclpyNode
from scout_msgs.msg import ScoutRCState, ScoutStatus
from std_msgs.msg import Bool


@pytest.mark.launch_test
def generate_test_description() -> LaunchDescription:
    """Launch the health monitor with only the chassis gate enabled."""
    config_file = PathJoinSubstitution(
        [FindPackageShare("scout_bringup"), "config", "system.yaml"]
    )
    return LaunchDescription(
        [
            Node(
                package="scout_bringup",
                executable="health_monitor",
                name="health_monitor",
                parameters=[
                    config_file,
                    {
                        "monitor_chassis": True,
                        "monitor_can_rx": False,
                        "monitor_lidar": False,
                        "monitor_tf": False,
                    },
                ],
                output="screen",
            ),
            launch_testing.actions.ReadyToTest(),
        ]
    )


class TestHealthMonitorPipeline(unittest.TestCase):
    """Exercise the fail-closed chassis gate through public ROS interfaces."""

    def setUp(self) -> None:
        rclpy.init()
        self.node = RclpyNode("test_health_monitor_pipeline")
        self.status_pub = self.node.create_publisher(
            ScoutStatus, "/scout_status", 10
        )
        self.rc_pub = self.node.create_publisher(ScoutRCState, "/rc_status", 10)
        self.odom_pub = self.node.create_publisher(Odometry, "/odom", 10)
        self.latest_fault = None
        self.diagnostics = {}
        self.estop_messages = 0
        self.node.create_subscription(
            Bool, "/safety/chassis_fault", self._on_fault, 10
        )
        self.node.create_subscription(
            Bool, "/safety/emergency_stop", self._on_estop, 10
        )
        self.node.create_subscription(
            DiagnosticArray, "/diagnostics", self._on_diagnostics, 10
        )

    def tearDown(self) -> None:
        self.node.destroy_node()
        rclpy.shutdown()

    def _on_fault(self, message: Bool) -> None:
        self.latest_fault = message.data

    def _on_estop(self, _message: Bool) -> None:
        self.estop_messages += 1

    def _on_diagnostics(self, message: DiagnosticArray) -> None:
        for status in message.status:
            self.diagnostics[status.name] = status

    def _publish_chassis(
        self,
        control_mode: int = 1,
        *,
        publish_status: bool = True,
        publish_rc: bool = True,
        publish_odom: bool = True,
    ) -> None:
        status = ScoutStatus()
        status.vehicle_state = 0
        status.control_mode = control_mode
        status.error_code = 0
        if publish_status:
            self.status_pub.publish(status)
        if publish_rc:
            self.rc_pub.publish(ScoutRCState())
        if publish_odom:
            self.odom_pub.publish(Odometry())

    def _wait_for_fault(
        self,
        expected_fault: bool,
        message_fragment: str,
        *,
        publish_control_mode: int | None = None,
        publish_rc: bool = True,
        publish_odom: bool = True,
        timeout_s: float = 5.0,
    ) -> DiagnosticStatus:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if publish_control_mode is not None:
                self._publish_chassis(
                    publish_control_mode,
                    publish_rc=publish_rc,
                    publish_odom=publish_odom,
                )
            rclpy.spin_once(self.node, timeout_sec=0.05)
            diagnostic = self.diagnostics.get("safety/chassis_fault")
            if (
                self.latest_fault is expected_fault
                and diagnostic is not None
                and message_fragment in diagnostic.message
            ):
                return diagnostic
        diagnostic = self.diagnostics.get("safety/chassis_fault")
        self.fail(
            f"expected chassis_fault={expected_fault} containing "
            f"{message_fragment!r}; last={self.latest_fault}, "
            f"diagnostic={None if diagnostic is None else diagnostic.message!r}"
        )

    def test_chassis_gate_and_unmonitored_estop_are_truthful(self) -> None:
        """All three chassis streams are required and e-stop is never forged."""
        missing = self._wait_for_fault(True, "scout status stale or missing")
        self.assertEqual(missing.level, DiagnosticStatus.ERROR)

        missing_rc = self._wait_for_fault(
            True,
            "rc status stale or missing",
            publish_control_mode=1,
            publish_rc=False,
            publish_odom=False,
        )
        self.assertEqual(missing_rc.level, DiagnosticStatus.ERROR)

        missing_odom = self._wait_for_fault(
            True,
            "odom stale or missing",
            publish_control_mode=1,
            publish_odom=False,
        )
        self.assertEqual(missing_odom.level, DiagnosticStatus.ERROR)

        nominal = self._wait_for_fault(False, "nominal", publish_control_mode=1)
        self.assertEqual(nominal.level, DiagnosticStatus.OK)

        wrong_mode = self._wait_for_fault(
            True,
            "control_mode=3 expected=1",
            publish_control_mode=3,
        )
        self.assertEqual(wrong_mode.level, DiagnosticStatus.ERROR)

        estop = self.diagnostics.get("safety/emergency_stop")
        self.assertIsNotNone(estop)
        self.assertEqual(estop.level, DiagnosticStatus.WARN)
        self.assertIn("unmonitored", estop.message)
        self.assertEqual(self.estop_messages, 0)
