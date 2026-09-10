"""Truthful safety and sensor health heartbeat publishers."""

from __future__ import annotations

import socket
import struct
import time
from typing import Any, Callable, Optional

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from scout_msgs.msg import ScoutRCState, ScoutStatus
from sensor_msgs.msg import Imu
from std_msgs.msg import Bool
from tf2_ros import Buffer, TransformListener

from scout_bringup.node_runner import run_node


SCOUT_SYSTEM_STATE_CAN_IDS = (0x211,)
CAN_FRAME = struct.Struct("=IB3x8s")


class ScoutCanRxMonitor:
    """Observe only real SCOUT Mini V2 system-state data frames."""

    def __init__(
        self,
        interface: str,
        *,
        timeout_ms: int,
        socket_factory: Callable[..., Any] = socket.socket,
    ) -> None:
        self._interface = interface
        self._timeout_ms = timeout_ms
        self._socket_factory = socket_factory
        self._socket = None
        self._last_state_frame_ns: Optional[int] = None
        self._previous_poll_ns: Optional[int] = None

    def _open(self) -> None:
        raw_socket = self._socket_factory(
            socket.PF_CAN,
            socket.SOCK_RAW,
            socket.CAN_RAW,
        )
        try:
            exact_standard_data_mask = (
                socket.CAN_SFF_MASK | socket.CAN_EFF_FLAG | socket.CAN_RTR_FLAG
            )
            filters = b"".join(
                struct.pack("=II", can_id, exact_standard_data_mask)
                for can_id in SCOUT_SYSTEM_STATE_CAN_IDS
            )
            raw_socket.setsockopt(
                socket.SOL_CAN_RAW,
                socket.CAN_RAW_FILTER,
                filters,
            )
            raw_socket.setsockopt(
                socket.SOL_CAN_RAW,
                socket.CAN_RAW_ERR_FILTER,
                struct.pack("=I", 0),
            )
            raw_socket.setblocking(False)
            raw_socket.bind((self._interface,))
        except OSError:
            raw_socket.close()
            raise
        self._socket = raw_socket

    def poll(self, *, now_ns: int) -> bool:
        """Drain state frames and report whether the latest is still fresh."""
        frame_time_lower_bound_ns = self._previous_poll_ns
        if self._socket is None:
            try:
                self._open()
            except OSError:
                self.close()
                return False
            frame_time_lower_bound_ns = now_ns
        elif frame_time_lower_bound_ns is None:
            frame_time_lower_bound_ns = now_ns

        while True:
            try:
                frame = self._socket.recv(CAN_FRAME.size)
            except BlockingIOError:
                break
            except OSError:
                self.close()
                return False
            if len(frame) != CAN_FRAME.size:
                continue
            can_id, dlc, _payload = CAN_FRAME.unpack(frame)
            if can_id & (
                socket.CAN_ERR_FLAG | socket.CAN_EFF_FLAG | socket.CAN_RTR_FLAG
            ):
                continue
            if dlc == 8 and (can_id & socket.CAN_SFF_MASK) in SCOUT_SYSTEM_STATE_CAN_IDS:
                self._last_state_frame_ns = frame_time_lower_bound_ns

        self._previous_poll_ns = now_ns
        if self._last_state_frame_ns is None:
            return False
        age_ns = now_ns - self._last_state_frame_ns
        return 0 <= age_ns <= self._timeout_ms * 1_000_000

    def close(self) -> None:
        """Close the observer and fail closed until a new state frame arrives."""
        if self._socket is not None:
            self._socket.close()
        self._socket = None
        self._last_state_frame_ns = None
        self._previous_poll_ns = None


class HealthMonitor(Node):
    """Publish only health states that can be established from real inputs."""

    def __init__(self) -> None:
        super().__init__("health_monitor")
        self.declare_parameter("lidar_topic", "/livox/imu")
        self.declare_parameter("monitor_lidar", True)
        self.declare_parameter("monitor_tf", True)
        self.declare_parameter("monitor_chassis", True)
        self.declare_parameter("monitor_can_rx", True)
        self.declare_parameter("monitor_emergency_stop", False)
        self.declare_parameter("lidar_timeout_ms", 1000)
        self.declare_parameter("chassis_timeout_ms", 500)
        self.declare_parameter("can_rx_timeout_ms", 500)
        self.declare_parameter("emergency_stop_timeout_ms", 500)
        self.declare_parameter("tf_source_frame", "odom")
        self.declare_parameter("tf_target_frame", "base_link")
        self.declare_parameter("tf_timeout_ms", 1000)
        self.declare_parameter("emergency_stop_topic", "/safety/emergency_stop")
        self.declare_parameter("chassis_fault_topic", "/safety/chassis_fault")
        self.declare_parameter("lidar_healthy_topic", "/safety/lidar_healthy")
        self.declare_parameter("tf_healthy_topic", "/safety/tf_healthy")
        self.declare_parameter("odom_tf_healthy_topic", "/safety/odom_tf_healthy")
        self.declare_parameter("scout_status_topic", "/scout_status")
        self.declare_parameter("scout_rc_topic", "/rc_status")
        self.declare_parameter("scout_odom_topic", "/odom")
        self.declare_parameter("can_interface", "can_scout")
        self.declare_parameter("expected_control_mode", 1)
        self.declare_parameter("expected_vehicle_state", 0)
        self.declare_parameter("diagnostics_topic", "/diagnostics")
        self.declare_parameter("default_chassis_fault", False)

        self._monitor_lidar = bool(self.get_parameter("monitor_lidar").value)
        self._monitor_tf = bool(self.get_parameter("monitor_tf").value)
        self._monitor_chassis = bool(self.get_parameter("monitor_chassis").value)
        self._monitor_can_rx = bool(self.get_parameter("monitor_can_rx").value)
        self._monitor_estop = bool(
            self.get_parameter("monitor_emergency_stop").value
        )
        self._lidar_timeout_ms = int(self.get_parameter("lidar_timeout_ms").value)
        self._chassis_timeout_ms = int(
            self.get_parameter("chassis_timeout_ms").value
        )
        self._can_rx_timeout_ms = int(
            self.get_parameter("can_rx_timeout_ms").value
        )
        self._estop_timeout_ms = int(
            self.get_parameter("emergency_stop_timeout_ms").value
        )
        self._tf_timeout_ms = int(self.get_parameter("tf_timeout_ms").value)
        self._lidar_topic = self.get_parameter("lidar_topic").value
        self._tf_source = self.get_parameter("tf_source_frame").value
        self._tf_target = self.get_parameter("tf_target_frame").value
        self._expected_control_mode = int(
            self.get_parameter("expected_control_mode").value
        )
        self._expected_vehicle_state = int(
            self.get_parameter("expected_vehicle_state").value
        )
        self._can_interface = str(self.get_parameter("can_interface").value)
        self._tf_healthy_topic = str(
            self.get_parameter("tf_healthy_topic").value
        )

        self._last_lidar_rx_ns: Optional[int] = None
        self._last_status_rx_ns: Optional[int] = None
        self._last_rc_rx_ns: Optional[int] = None
        self._last_odom_rx_ns: Optional[int] = None
        self._last_estop_rx_ns: Optional[int] = None
        self._estop_active = False
        self._control_mode = 0
        self._vehicle_state = 0
        self._chassis_error_code = 0
        self._can_rx_monitor = ScoutCanRxMonitor(
            self._can_interface,
            timeout_ms=self._can_rx_timeout_ms,
        )
        self._default_chassis_fault = bool(
            self.get_parameter("default_chassis_fault").value
        )

        self._fault_pub = self.create_publisher(
            Bool, self.get_parameter("chassis_fault_topic").value, 10
        )
        self._lidar_pub = self.create_publisher(
            Bool, self.get_parameter("lidar_healthy_topic").value, 10
        )
        self._tf_pub = self.create_publisher(
            Bool, self._tf_healthy_topic, 10
        )
        self._diag_pub = self.create_publisher(
            DiagnosticArray, self.get_parameter("diagnostics_topic").value, 10
        )

        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        if self._monitor_lidar:
            self.create_subscription(
                Imu, self._lidar_topic, self._on_lidar, qos_profile_sensor_data
            )
        if self._monitor_chassis:
            self.create_subscription(
                ScoutStatus,
                self.get_parameter("scout_status_topic").value,
                self._on_chassis_status,
                qos_profile_sensor_data,
            )
            self.create_subscription(
                ScoutRCState,
                self.get_parameter("scout_rc_topic").value,
                self._on_rc_status,
                qos_profile_sensor_data,
            )
            self.create_subscription(
                Odometry,
                self.get_parameter("scout_odom_topic").value,
                self._on_odom,
                qos_profile_sensor_data,
            )
        if self._monitor_estop:
            self.create_subscription(
                Bool,
                self.get_parameter("emergency_stop_topic").value,
                self._on_estop,
                10,
            )
        self.create_timer(0.1, self._publish_health)

    def _now_ns(self) -> int:
        return time.monotonic_ns()

    def _on_lidar(self, _msg: Imu) -> None:
        self._last_lidar_rx_ns = self._now_ns()

    def _on_chassis_status(self, msg: ScoutStatus) -> None:
        self._last_status_rx_ns = self._now_ns()
        self._control_mode = int(msg.control_mode)
        self._vehicle_state = int(msg.vehicle_state)
        self._chassis_error_code = int(msg.error_code)

    def _on_rc_status(self, _msg: ScoutRCState) -> None:
        self._last_rc_rx_ns = self._now_ns()

    def _on_odom(self, _msg: Odometry) -> None:
        self._last_odom_rx_ns = self._now_ns()

    def _on_estop(self, msg: Bool) -> None:
        self._last_estop_rx_ns = self._now_ns()
        self._estop_active = bool(msg.data)

    def _is_fresh(self, last_rx_ns: Optional[int], timeout_ms: int) -> bool:
        if last_rx_ns is None:
            return False
        age_ms = (self._now_ns() - last_rx_ns) / 1_000_000
        return 0 <= age_ms <= timeout_ms

    def _lookup_tf_ok(self) -> bool:
        try:
            return bool(
                self._tf_buffer.can_transform(
                    self._tf_target,
                    self._tf_source,
                    rclpy.time.Time(),
                    timeout=rclpy.duration.Duration(
                        seconds=0,
                        nanoseconds=int(self._tf_timeout_ms * 1_000_000),
                    ),
                )
            )
        except Exception:
            return False

    def _can_rx_fresh(self) -> bool:
        return self._can_rx_monitor.poll(now_ns=self._now_ns())

    def _chassis_fault_and_message(self) -> tuple[bool, str]:
        if not self._monitor_chassis:
            return self._default_chassis_fault, "unmonitored (configured default)"
        if self._monitor_can_rx and not self._can_rx_fresh():
            return True, f"CAN RX stale or missing on {self._can_interface}"
        if not self._is_fresh(self._last_status_rx_ns, self._chassis_timeout_ms):
            return True, "scout status stale or missing"
        if not self._is_fresh(self._last_rc_rx_ns, self._chassis_timeout_ms):
            return True, "rc status stale or missing"
        if not self._is_fresh(self._last_odom_rx_ns, self._chassis_timeout_ms):
            return True, "odom stale or missing"
        if self._vehicle_state != self._expected_vehicle_state:
            return (
                True,
                f"vehicle_state={self._vehicle_state} "
                f"expected={self._expected_vehicle_state}",
            )
        if self._control_mode != self._expected_control_mode:
            return (
                True,
                f"control_mode={self._control_mode} "
                f"expected={self._expected_control_mode}",
            )
        if self._chassis_error_code != 0:
            return True, f"scout error_code={self._chassis_error_code}"
        return False, "nominal"

    def _estop_status(self) -> DiagnosticStatus:
        if not self._monitor_estop:
            return self._make_status(
                "safety/emergency_stop",
                DiagnosticStatus.WARN,
                "unmonitored (physical e-stop independent)",
                None,
                monitored=False,
            )
        if not self._is_fresh(self._last_estop_rx_ns, self._estop_timeout_ms):
            return self._make_status(
                "safety/emergency_stop",
                DiagnosticStatus.ERROR,
                "emergency stop stale or missing",
                None,
                monitored=True,
            )
        return self._make_status(
            "safety/emergency_stop",
            DiagnosticStatus.ERROR if self._estop_active else DiagnosticStatus.OK,
            "active" if self._estop_active else "released",
            self._estop_active,
            monitored=True,
        )

    def _publish_health(self) -> None:
        now = self.get_clock().now()
        chassis_fault, chassis_message = self._chassis_fault_and_message()
        fault = Bool(data=chassis_fault)
        lidar_ok = Bool(
            data=not self._monitor_lidar
            or self._is_fresh(self._last_lidar_rx_ns, self._lidar_timeout_ms)
        )
        tf_ok = Bool(data=not self._monitor_tf or self._lookup_tf_ok())

        self._fault_pub.publish(fault)
        self._lidar_pub.publish(lidar_ok)
        self._tf_pub.publish(tf_ok)

        statuses = [
            self._estop_status(),
            self._make_status(
                "safety/chassis_fault",
                DiagnosticStatus.ERROR if fault.data else DiagnosticStatus.OK,
                chassis_message,
                fault.data,
                monitored=self._monitor_chassis,
            ),
            self._make_status(
                "safety/lidar_healthy",
                DiagnosticStatus.OK if lidar_ok.data else DiagnosticStatus.ERROR,
                "unmonitored (stub true)"
                if not self._monitor_lidar
                else ("stream healthy" if lidar_ok.data else "lidar stale or missing"),
                lidar_ok.data,
                monitored=self._monitor_lidar,
            ),
            self._make_status(
                self._tf_healthy_topic.lstrip("/"),
                DiagnosticStatus.OK if tf_ok.data else DiagnosticStatus.ERROR,
                "unmonitored (stub true)"
                if not self._monitor_tf
                else ("tf available" if tf_ok.data else "tf lookup failed"),
                tf_ok.data,
                monitored=self._monitor_tf,
            ),
        ]

        diag = DiagnosticArray()
        diag.header.stamp = now.to_msg()
        diag.status = statuses
        self._diag_pub.publish(diag)

    @staticmethod
    def _make_status(
        name: str,
        level: int,
        message: str,
        value: Optional[bool],
        *,
        monitored: bool,
    ) -> DiagnosticStatus:
        status = DiagnosticStatus()
        status.name = name
        status.level = level
        status.message = message
        status.values = [
            KeyValue(key="monitored", value=str(monitored).lower()),
            KeyValue(
                key="value",
                value="unknown" if value is None else str(value).lower(),
            ),
        ]
        return status


def main(args=None) -> None:
    run_node(HealthMonitor, args=args)


if __name__ == "__main__":
    main()
