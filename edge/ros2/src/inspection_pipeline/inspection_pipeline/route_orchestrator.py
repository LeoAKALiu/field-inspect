"""ROS adapter supervising Nav2 FollowPath over a frozen fixed route."""

from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path
from typing import Optional

import rclpy
from action_msgs.msg import GoalStatus
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import FollowPath
from nav_msgs.msg import Path as PathMessage, Odometry
from sensor_msgs.msg import Image
from inspection_pipeline.station_capture import StationCapture
from rclpy.action import ActionClient
from rclpy.node import Node
from std_srvs.srv import Trigger

from inspection_pipeline.fixed_route import (
    HARD_MAX_LINEAR_MPS,
    FixedRouteError,
    RouteCommand,
    RouteMachine,
    RouteState,
    RouteTransition,
    load_fixed_route,
    route_pose_length,
)

ACTIVE_SAFETY_REASONS = frozenset({"auto", "auto_zero"})
MOVING_STATES = frozenset({RouteState.ARMING, RouteState.EXECUTING})


def _monotonic_ns() -> int:
    return time.monotonic_ns()


class RouteOrchestrator(Node):
    """Expose operator controls and latch every route interruption."""

    def __init__(self, *, parameter_overrides=None) -> None:
        super().__init__(
            "route_orchestrator",
            parameter_overrides=parameter_overrides,
        )
        self.declare_parameter("fixed_route_path", "")
        self.declare_parameter("allow_unverified_route_for_testing", False)
        self.declare_parameter("controller_tuning_verified", False)
        self.declare_parameter("max_linear_mps", HARD_MAX_LINEAR_MPS)
        self.declare_parameter("route_status_topic", "/route/status")
        self.declare_parameter("safety_diagnostics_topic", "/diagnostics")
        self.declare_parameter("inspection_status_topic", "/inspection/status")
        self.declare_parameter("auto_command_topic", "/cmd_vel_auto")
        self.declare_parameter("expected_auto_publisher_node", "controller_server")
        self.declare_parameter("follow_path_action", "/follow_path")
        self.declare_parameter("controller_id", "FollowPath")
        self.declare_parameter("goal_checker_id", "goal_checker")
        self.declare_parameter("first_command_timeout_ms", 1000)
        self.declare_parameter("safety_diagnostic_timeout_ms", 500)
        self.declare_parameter("inspection_status_timeout_ms", 2500)

        route_path = Path(str(self.get_parameter("fixed_route_path").value))
        if not bool(self.get_parameter("controller_tuning_verified").value):
            raise FixedRouteError("controller tuning is not verified for onsite execution")
        allow_unverified = bool(
            self.get_parameter("allow_unverified_route_for_testing").value
        )
        self._route = load_fixed_route(
            route_path,
            allow_unverified=allow_unverified,
        )
        if allow_unverified:
            self.get_logger().warn(
                "allow_unverified_route_for_testing=true; this run cannot be onsite evidence"
            )
        configured_speed = float(self.get_parameter("max_linear_mps").value)
        if (
            not math.isfinite(configured_speed)
            or configured_speed <= 0.0
            or configured_speed > HARD_MAX_LINEAR_MPS
        ):
            raise FixedRouteError("max_linear_mps must be in (0.0, 0.2]")
        if self._route.approved_max_linear_mps > configured_speed:
            raise FixedRouteError(
                "fixed route approved speed exceeds the configured controller limit"
            )

        self.declare_parameter("station_pose_topic", "/Odometry")
        self.declare_parameter("station_image_topic", "/camera/image_raw")
        self._capture = StationCapture()
        self._capture_run = None
        self._capture_run_id = ""
        self._machine = RouteMachine(self._route)
        self._first_command_timeout_ns = self._positive_timeout_ns(
            "first_command_timeout_ms"
        )
        self._safety_timeout_ns = self._positive_timeout_ns(
            "safety_diagnostic_timeout_ms"
        )
        self._inspection_timeout_ns = self._positive_timeout_ns(
            "inspection_status_timeout_ms"
        )
        self._controller_id = str(self.get_parameter("controller_id").value)
        self._goal_checker_id = str(self.get_parameter("goal_checker_id").value)
        action_name = str(self.get_parameter("follow_path_action").value)
        self._auto_command_topic = str(self.get_parameter("auto_command_topic").value)
        self._expected_auto_publisher = str(
            self.get_parameter("expected_auto_publisher_node").value
        )

        self._goal_generation = 0
        self._goal_handle = None
        self._dispatch_ns = 0
        self._goal_accepted_ns = 0
        self._auto_after_goal_acceptance = False
        self._safety_after_dispatch = False
        self._last_safety_ns = 0
        self._last_safety_reason = "missing"
        self._last_inspection_ns = 0
        self._inspection_running = False
        self._inspection_production = False
        self._distance_to_goal_m: Optional[float] = None
        self._controller_speed_mps: Optional[float] = None
        self._execution_id = ""
        self._execution_started_ns = 0

        status_topic = str(self.get_parameter("route_status_topic").value)
        safety_topic = str(self.get_parameter("safety_diagnostics_topic").value)
        inspection_topic = str(self.get_parameter("inspection_status_topic").value)
        self._status_pub = self.create_publisher(DiagnosticArray, status_topic, 10)
        self.create_subscription(
            DiagnosticArray,
            safety_topic,
            self._on_safety_diagnostics,
            10,
        )
        self.create_subscription(
            Twist,
            self._auto_command_topic,
            self._on_auto_command,
            10,
        )
        self.create_subscription(
            DiagnosticArray,
            inspection_topic,
            self._on_inspection_status,
            10,
        )
        self.create_subscription(Odometry, str(self.get_parameter("station_pose_topic").value),
                                 self._capture_odometry, 10)
        self.create_subscription(Image, str(self.get_parameter("station_image_topic").value),
                                 self._capture_image, 10)
        self.create_service(Trigger, "route/retry_capture", self._retry_capture)
        self._follow_path = ActionClient(self, FollowPath, action_name)
        self.create_service(Trigger, "route/start", self._start)
        self.create_service(Trigger, "route/pause", self._pause)
        self.create_service(Trigger, "route/resume", self._resume)
        self.create_service(Trigger, "route/cancel", self._cancel)
        self.create_service(Trigger, "route/complete_station", self._complete_station)
        self.create_service(Trigger, "route/skip_station", self._skip_station)
        self.create_timer(0.1, self._watchdog)
        self.create_timer(0.2, self._publish_status)

    def _positive_timeout_ns(self, parameter: str) -> int:
        value = self.get_parameter(parameter).value
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise FixedRouteError(f"{parameter} must be a positive integer")
        return value * 1_000_000

    def _inspection_ready(self, now_ns: Optional[int] = None) -> bool:
        now = _monotonic_ns() if now_ns is None else now_ns
        return (
            self._inspection_running
            and self._inspection_production
            and self._last_inspection_ns > 0
            and now - self._last_inspection_ns <= self._inspection_timeout_ns
        )

    def _safety_ready(self, now_ns: Optional[int] = None) -> bool:
        now = _monotonic_ns() if now_ns is None else now_ns
        return (
            self._last_safety_reason in ACTIVE_SAFETY_REASONS
            and self._last_safety_ns > 0
            and now - self._last_safety_ns <= self._safety_timeout_ns
        )

    def _motion_start_ready(self) -> tuple[bool, str]:
        if not self._inspection_ready():
            return False, "inspection run is not active and fresh"
        if not self._safety_ready():
            return False, "safety state is not fresh and motion-permitting"
        if not self._follow_path.server_is_ready():
            return False, "FollowPath action server is unavailable or inactive"
        publishers = self.get_publishers_info_by_topic(self._auto_command_topic)
        if len(publishers) != 1 or publishers[0].node_name != self._expected_auto_publisher:
            observed = ", ".join(
                f"{item.node_namespace}/{item.node_name}" for item in publishers
            ) or "none"
            return (
                False,
                "auto command topic must have exactly one controller publisher; "
                f"observed: {observed}",
            )
        return True, ""

    def _respond(
        self,
        response: Trigger.Response,
        transition: RouteTransition,
    ) -> Trigger.Response:
        dispatched = self._apply_command(transition.command)
        response.success = transition.accepted and dispatched
        response.message = (
            transition.reason if dispatched else self._machine.reason
        )
        return response

    def _start(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        del request
        ready, message = self._motion_start_ready()
        if not ready:
            response.success = False
            response.message = message
            return response
        transition = self._machine.start()
        if transition.accepted:
            self._execution_id = uuid.uuid4().hex[:12]
            self._execution_started_ns = self.get_clock().now().nanoseconds
        return self._respond(response, transition)

    def _pause(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        del request
        return self._respond(response, self._machine.pause())

    def _resume(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        del request
        ready, message = self._motion_start_ready()
        if not ready:
            response.success = False
            response.message = message
            return response
        return self._respond(response, self._machine.resume())

    def _cancel(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        del request
        return self._respond(response, self._machine.cancel())

    def _complete_station(
        self,
        request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        del request
        return self._record_station(response, skipped=False)

    def _skip_station(
        self,
        request: Trigger.Request,
        response: Trigger.Response,
    ) -> Trigger.Response:
        del request
        return self._record_station(response, skipped=True)

    def _record_station(
        self,
        response: Trigger.Response,
        *,
        skipped: bool,
    ) -> Trigger.Response:
        if (
            self._machine.state == RouteState.AT_STATION
            and self._machine.station_index + 1 < len(self._route.stations)
        ):
            ready, message = self._motion_start_ready()
            if not ready:
                response.success = False
                response.message = message
                return response
        if not skipped and not self._capture.ready:
            response.success = False
            response.message = self._capture.reason
            return response
        if skipped:
            self._capture.fail("operator_skipped_capture")
        transition = self._machine.record_station(skipped=skipped)
        if transition.accepted:
            self._capture.leave()
        return self._respond(response, transition)

    def _apply_command(self, command: RouteCommand) -> bool:
        if command == RouteCommand.CANCEL_GOAL:
            self._invalidate_and_cancel_goal()
        elif command == RouteCommand.SEND_SEGMENT:
            return self._dispatch_segment()
        return True

    def _path_message(self) -> PathMessage:
        stamp = self.get_clock().now().to_msg()
        message = PathMessage()
        message.header.stamp = stamp
        message.header.frame_id = self._route.frame_id
        for pose in self._machine.current_segment():
            item = PoseStamped()
            item.header = message.header
            item.pose.position.x = pose.x_m
            item.pose.position.y = pose.y_m
            item.pose.orientation.z = math.sin(pose.yaw_rad / 2.0)
            item.pose.orientation.w = math.cos(pose.yaw_rad / 2.0)
            message.poses.append(item)
        return message

    def _dispatch_segment(self) -> bool:
        if not self._follow_path.server_is_ready():
            self._machine.controller_failed("follow_path_server_unavailable")
            return False
        self._invalidate_and_cancel_goal()
        self._goal_generation += 1
        generation = self._goal_generation
        self._dispatch_ns = _monotonic_ns()
        self._goal_accepted_ns = 0
        self._auto_after_goal_acceptance = False
        self._safety_after_dispatch = False
        self._distance_to_goal_m = None
        self._controller_speed_mps = None

        goal = FollowPath.Goal()
        goal.path = self._path_message()
        goal.controller_id = self._controller_id
        goal.goal_checker_id = self._goal_checker_id
        future = self._follow_path.send_goal_async(
            goal,
            feedback_callback=lambda message: self._on_feedback(generation, message),
        )
        future.add_done_callback(
            lambda completed: self._on_goal_response(generation, completed)
        )
        return True

    def _invalidate_and_cancel_goal(self) -> None:
        self._goal_generation += 1
        self._goal_accepted_ns = 0
        self._auto_after_goal_acceptance = False
        handle = self._goal_handle
        self._goal_handle = None
        if handle is not None:
            handle.cancel_goal_async()

    def _on_goal_response(self, generation: int, future) -> None:
        if generation != self._goal_generation:
            try:
                stale_handle = future.result()
            except Exception:
                return
            if stale_handle.accepted:
                stale_handle.cancel_goal_async()
            return
        try:
            handle = future.result()
        except Exception as exc:  # rclpy futures surface transport errors here
            self.get_logger().error(f"FollowPath goal request failed: {exc}")
            self._machine.controller_failed("follow_path_goal_request_failed")
            return
        if not handle.accepted:
            self._machine.controller_failed("follow_path_goal_rejected")
            return
        self._goal_handle = handle
        self._goal_accepted_ns = _monotonic_ns()
        self._auto_after_goal_acceptance = False
        result_future = handle.get_result_async()
        result_future.add_done_callback(
            lambda completed: self._on_result(generation, completed)
        )

    def _on_feedback(self, generation: int, message) -> None:
        if generation != self._goal_generation or self._machine.state not in MOVING_STATES:
            return
        distance = float(message.feedback.distance_to_goal)
        speed = float(message.feedback.speed)
        if not math.isfinite(distance) or distance < 0.0 or not math.isfinite(speed):
            transition = self._machine.pause("invalid_follow_path_feedback")
            self._apply_command(transition.command)
            return
        self._distance_to_goal_m = distance
        self._controller_speed_mps = speed

    def _on_result(self, generation: int, future) -> None:
        if generation != self._goal_generation:
            return
        self._goal_handle = None
        try:
            wrapped = future.result()
        except Exception as exc:  # rclpy futures surface transport errors here
            self.get_logger().error(f"FollowPath result failed: {exc}")
            self._machine.controller_failed("follow_path_result_failed")
            return
        if wrapped.status == GoalStatus.STATUS_SUCCEEDED:
            transition = self._machine.reached_station()
            if transition.accepted:
                self._begin_capture()
        else:
            self._machine.controller_failed(f"follow_path_status_{wrapped.status}")

    def _on_safety_diagnostics(self, message: DiagnosticArray) -> None:
        for status in message.status:
            if status.name != "safety/safety_mux":
                continue
            now_ns = _monotonic_ns()
            self._last_safety_ns = now_ns
            self._last_safety_reason = status.message
            if self._machine.state == RouteState.ARMING:
                self._safety_after_dispatch = True
                if status.message not in ACTIVE_SAFETY_REASONS:
                    transition = self._machine.pause(f"safety_stop:{status.message}")
                    self._apply_command(transition.command)
                elif (
                    self._goal_accepted_ns > 0
                    and self._auto_after_goal_acceptance
                ):
                    self._machine.controller_active()
            elif (
                self._machine.state == RouteState.EXECUTING
                and status.message not in ACTIVE_SAFETY_REASONS
            ):
                transition = self._machine.pause(f"safety_stop:{status.message}")
                self._apply_command(transition.command)
            return

    def _on_auto_command(self, message: Twist) -> None:
        if self._machine.state not in MOVING_STATES or self._goal_accepted_ns <= 0:
            return
        if not math.isfinite(message.linear.x) or not math.isfinite(message.angular.z):
            transition = self._machine.pause("invalid_auto_command")
            self._apply_command(transition.command)
            return
        self._auto_after_goal_acceptance = True

    def _on_inspection_status(self, message: DiagnosticArray) -> None:
        for status in message.status:
            if status.name != "inspection_pipeline":
                continue
            self._last_inspection_ns = _monotonic_ns()
            self._inspection_running = status.message == "running"
            values = {item.key: item.value for item in status.values}
            self._inspection_production = values.get("run_mode") == "production"
            raw_dir = values.get("run_dir", "")
            self._capture_run = Path(raw_dir) if raw_dir else None
            self._capture_run_id = values.get("run_id", "")
            if not self._inspection_running and self._machine.state in MOVING_STATES:
                transition = self._machine.pause("inspection_run_not_running")
                self._apply_command(transition.command)
            return

    def _begin_capture(self):
        if self._capture_run is None or not self._inspection_ready():
            self._capture.reason = "active_run_directory_unavailable"
            return
        station = self._machine.current_station
        if station is not None:
            try:
                self._capture.begin(self._capture_run, self._capture_run_id,
                                    self._execution_id, station.station_id)
            except OSError:
                self._capture.failed = True
                self._capture.reason = "station_storage_unavailable"

    def _capture_odometry(self, message):
        if self._machine.state == RouteState.AT_STATION and self._inspection_ready():
            self._capture.odometry(message, self._route.frame_id)

    def _capture_image(self, message):
        if self._machine.state == RouteState.AT_STATION and self._inspection_ready():
            try:
                self._capture.image(message)
            except OSError:
                self._capture.failed = True
                self._capture.reason = "station_storage_unavailable"

    def _retry_capture(self, request, response):
        del request
        if self._machine.state != RouteState.AT_STATION or not self._inspection_ready():
            response.success = False
            response.message = "retry requires an active stationary inspection station"
            return response
        try:
            self._capture.leave()
            self._begin_capture()
        except OSError:
            self._capture.failed = True
        response.success = not self._capture.failed
        response.message = self._capture.reason
        return response

    def _watchdog(self) -> None:
        now_ns = _monotonic_ns()
        try:
            if self._machine.state != RouteState.AT_STATION or not self._inspection_ready(now_ns):
                self._capture.leave()
            else:
                self._capture.tick()
        except OSError:
            self._capture.failed = True
            self._capture.ready = False
            self._capture.reason = "station_storage_unavailable"
        if self._machine.state == RouteState.ARMING:
            if not self._inspection_ready(now_ns):
                transition = self._machine.pause("inspection_status_missing_or_stale")
                self._apply_command(transition.command)
            elif now_ns - self._dispatch_ns > self._first_command_timeout_ns:
                reason = (
                    "safe_auto_command_timeout"
                    if self._safety_after_dispatch
                    else "safety_diagnostics_missing_during_arming"
                )
                transition = self._machine.pause(reason)
                self._apply_command(transition.command)
        elif self._machine.state == RouteState.EXECUTING:
            if not self._inspection_ready(now_ns):
                transition = self._machine.pause("inspection_status_missing_or_stale")
                self._apply_command(transition.command)
            elif (
                self._last_safety_ns <= 0
                or now_ns - self._last_safety_ns > self._safety_timeout_ns
            ):
                transition = self._machine.pause("safety_diagnostics_missing_or_stale")
                self._apply_command(transition.command)

    def _publish_status(self) -> None:
        current_station = self._machine.current_station
        level = DiagnosticStatus.STALE
        if self._machine.state in {RouteState.ARMING, RouteState.EXECUTING}:
            level = DiagnosticStatus.OK
        elif self._machine.state in {
            RouteState.PAUSED,
            RouteState.COMPLETED_WITH_EXCEPTIONS,
        }:
            level = DiagnosticStatus.WARN
        elif self._machine.state in {
            RouteState.AT_STATION,
            RouteState.COMPLETED,
            RouteState.CANCELED,
        }:
            level = DiagnosticStatus.OK

        status = DiagnosticStatus()
        status.name = "inspection_pipeline/route_orchestrator"
        status.hardware_id = self._route.route_id
        status.level = level
        status.message = self._machine.reason
        status.values = [
            KeyValue(key="capture_state", value=self._capture.reason),
            KeyValue(key="capture_attempt", value=str(self._capture.attempt)),
            KeyValue(key="capture_persisted", value=str(self._capture.ready).lower()),
            KeyValue(key="route_id", value=self._route.route_id),
            KeyValue(key="route_sha256", value=self._route.source_sha256),
            KeyValue(key="execution_id", value=self._execution_id),
            KeyValue(
                key="execution_started_ns",
                value=str(self._execution_started_ns) if self._execution_started_ns else "",
            ),
            KeyValue(key="route_state", value=self._machine.state.value),
            KeyValue(key="reason", value=self._machine.reason),
            KeyValue(
                key="station_id",
                value="" if current_station is None else current_station.station_id,
            ),
            KeyValue(key="station_index", value=str(self._machine.station_index)),
            KeyValue(key="station_count", value=str(len(self._route.stations))),
            KeyValue(
                key="station_outcomes",
                value=json.dumps(
                    [item.__dict__ for item in self._machine.outcomes],
                    separators=(",", ":"),
                ),
            ),
            KeyValue(
                key="route_length_m",
                value=f"{route_pose_length(self._route.poses):.6f}",
            ),
            KeyValue(key="last_safety_reason", value=self._last_safety_reason),
            KeyValue(
                key="distance_to_goal_m",
                value=""
                if self._distance_to_goal_m is None
                else f"{self._distance_to_goal_m:.6f}",
            ),
            KeyValue(
                key="controller_speed_mps",
                value=""
                if self._controller_speed_mps is None
                else f"{self._controller_speed_mps:.6f}",
            ),
        ]
        message = DiagnosticArray()
        message.header.stamp = self.get_clock().now().to_msg()
        message.status = [status]
        self._status_pub.publish(message)

    def destroy_node(self) -> bool:
        self._invalidate_and_cancel_goal()
        self._follow_path.destroy()
        return super().destroy_node()


def main(args=None) -> None:
    """Run the supervised fixed-route node."""
    rclpy.init(args=args)
    node = RouteOrchestrator()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
