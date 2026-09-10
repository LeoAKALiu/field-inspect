"""ROS integration test for supervised FollowPath cancellation and resume."""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path

import pytest
import rclpy
import yaml

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue  # noqa: E402
from geometry_msgs.msg import Twist  # noqa: E402
from rclpy.action import ActionServer, CancelResponse  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402
from std_srvs.srv import Trigger  # noqa: E402

NAV2_MSGS_AVAILABLE = importlib.util.find_spec("nav2_msgs") is not None
if NAV2_MSGS_AVAILABLE:
    from nav2_msgs.action import FollowPath  # noqa: E402

    from inspection_pipeline.route_orchestrator import RouteOrchestrator  # noqa: E402
else:
    FollowPath = None
    RouteOrchestrator = None


def _write_route(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "route_id": "integration-route-v1",
                "source_kind": "synthetic_contract_fixture",
                "frame_id": "map",
                "verified": False,
                "verified_at": None,
                "source_reference": "",
                "approved_max_linear_mps": 0.2,
                "poses": [
                    {"x_m": 0.0, "y_m": 0.0, "yaw_rad": 0.0},
                    {"x_m": 1.0, "y_m": 0.0, "yaw_rad": 0.0},
                ],
                "stations": [
                    {
                        "station_id": "station-001",
                        "pose_index": 1,
                        "required": True,
                        "tag_ids": [],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


class FakeController(Node):
    """Minimal FollowPath server with the expected command publisher identity."""

    def __init__(self) -> None:
        super().__init__("controller_server")
        self.command_pub = self.create_publisher(Twist, "/cmd_vel_auto", 10)
        self.goal_count = 0
        self.cancel_count = 0
        self.server = ActionServer(
            self,
            FollowPath,
            "/follow_path",
            execute_callback=self._execute,
            cancel_callback=lambda _request: CancelResponse.ACCEPT,
        )

    def _execute(self, goal_handle):
        self.goal_count += 1
        while rclpy.ok():
            if goal_handle.is_cancel_requested:
                self.cancel_count += 1
                goal_handle.canceled()
                return FollowPath.Result()
            command = Twist()
            command.linear.x = 0.1
            self.command_pub.publish(command)
            feedback = FollowPath.Feedback()
            feedback.distance_to_goal = 0.5
            feedback.speed = 0.1
            goal_handle.publish_feedback(feedback)
            time.sleep(0.02)
        goal_handle.abort()
        return FollowPath.Result()

    def destroy_node(self) -> bool:
        self.server.destroy()
        return super().destroy_node()


class OperatorHarness(Node):
    """Publish run/safety heartbeats and invoke the operator services."""

    def __init__(self) -> None:
        super().__init__("route_operator_test")
        self.safety_reason = "auto"
        self.latest_route_state = ""
        self.inspection_pub = self.create_publisher(
            DiagnosticArray,
            "/inspection/status",
            10,
        )
        self.safety_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.create_subscription(
            DiagnosticArray,
            "/route/status",
            self._on_route_status,
            10,
        )
        self.create_timer(0.05, self._publish_heartbeats)

    def _publish_heartbeats(self) -> None:
        inspection = DiagnosticStatus()
        inspection.name = "inspection_pipeline"
        inspection.message = "running"
        inspection.values = [KeyValue(key="run_mode", value="production")]
        inspection_message = DiagnosticArray()
        inspection_message.header.stamp = self.get_clock().now().to_msg()
        inspection_message.status = [inspection]
        self.inspection_pub.publish(inspection_message)

        safety = DiagnosticStatus()
        safety.name = "safety/safety_mux"
        safety.message = self.safety_reason
        safety_message = DiagnosticArray()
        safety_message.header.stamp = self.get_clock().now().to_msg()
        safety_message.status = [safety]
        self.safety_pub.publish(safety_message)

    def _on_route_status(self, message: DiagnosticArray) -> None:
        for status in message.status:
            if status.name != "inspection_pipeline/route_orchestrator":
                continue
            values = {item.key: item.value for item in status.values}
            self.latest_route_state = values.get("route_state", "")


def _wait_for(predicate, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


def _call(node: Node, service_name: str):
    client = node.create_client(Trigger, service_name)
    assert client.wait_for_service(timeout_sec=3.0)
    future = client.call_async(Trigger.Request())
    _wait_for(future.done)
    return future.result()


@pytest.mark.skipif(not NAV2_MSGS_AVAILABLE, reason="nav2_msgs is not installed")
def test_safety_stop_cancels_and_explicit_resume_sends_a_new_goal(tmp_path: Path) -> None:
    """A safety stop must latch even after the heartbeat reports healthy again."""
    route_path = tmp_path / "route.yaml"
    _write_route(route_path)
    rclpy.init()
    controller = FakeController()
    operator = OperatorHarness()
    orchestrator = RouteOrchestrator(
        parameter_overrides=[
            Parameter("fixed_route_path", value=str(route_path)),
            Parameter("allow_unverified_route_for_testing", value=True),
            Parameter("controller_tuning_verified", value=True),
            Parameter("first_command_timeout_ms", value=1000),
        ]
    )
    executor = MultiThreadedExecutor(num_threads=4)
    for node in (controller, operator, orchestrator):
        executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    try:
        _wait_for(lambda: orchestrator._follow_path.server_is_ready())
        _wait_for(lambda: orchestrator._inspection_ready())
        _wait_for(lambda: orchestrator._safety_ready())
        started = _call(operator, "/route/start")
        assert started.success is True
        _wait_for(lambda: operator.latest_route_state == "executing")
        assert controller.goal_count == 1

        operator.safety_reason = "obstacle_stop"
        _wait_for(lambda: operator.latest_route_state == "paused")
        _wait_for(lambda: controller.cancel_count == 1)

        blocked_resume = _call(operator, "/route/resume")
        assert blocked_resume.success is False
        assert "safety state" in blocked_resume.message
        assert controller.goal_count == 1

        operator.safety_reason = "auto"
        _wait_for(lambda: orchestrator._safety_ready())
        time.sleep(0.2)
        assert operator.latest_route_state == "paused"

        resumed = _call(operator, "/route/resume")
        assert resumed.success is True
        _wait_for(lambda: operator.latest_route_state == "executing")
        assert controller.goal_count == 2
    finally:
        _call(operator, "/route/cancel")
        executor.shutdown(timeout_sec=3.0)
        spin_thread.join(timeout=3.0)
        for node in (orchestrator, operator, controller):
            node.destroy_node()
        rclpy.shutdown()
