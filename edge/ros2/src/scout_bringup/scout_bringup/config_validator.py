"""Validate the unified ``system.yaml`` parameter contract."""

from __future__ import annotations

import ipaddress
import math
import re
from pathlib import Path
from typing import Any

import yaml

REQUIRED_SECTIONS = (
    "scout_bringup",
    "hik_camera_node",
    "health_monitor",
    "localization_adapter",
    "safety_mux",
    "joy_node",
    "teleop_twist_joy",
    "collision_monitor",
    "route_orchestrator",
    "inspection_manager",
)

ALLOWED_BACKENDS = frozenset({"mvs", "aravis"})
ALLOWED_PIXEL_FORMATS = frozenset({"BGR8"})
TOPIC_PATTERN = re.compile(r"^/[A-Za-z0-9_/-]+$")
FRAME_PATTERN = re.compile(r"^[A-Za-z0-9_/-]+$")
CAMERA_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
CALIBRATION_URL_PATTERN = re.compile(r"^(file|package)://.+$")
RUN_MODES = frozenset({"commissioning", "production"})


def _require_string(
    errors: list[str],
    section: str,
    key: str,
    value: Any,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, str):
        errors.append(f"{section}.{key} must be a string")
        return
    if not allow_empty and not value.strip():
        errors.append(f"{section}.{key} must not be empty")


def _require_topic(errors: list[str], section: str, key: str, value: Any) -> None:
    _require_string(errors, section, key, value)
    if isinstance(value, str) and value and not TOPIC_PATTERN.match(value):
        errors.append(f"{section}.{key} must be an absolute ROS topic path")


def _require_frame(errors: list[str], section: str, key: str, value: Any) -> None:
    _require_string(errors, section, key, value)
    if isinstance(value, str) and value and not FRAME_PATTERN.match(value):
        errors.append(f"{section}.{key} must be a valid frame id")


def _require_ip(errors: list[str], section: str, key: str, value: Any) -> None:
    _require_string(errors, section, key, value)
    if not isinstance(value, str) or not value:
        return
    try:
        ipaddress.ip_address(value)
    except ValueError:
        errors.append(f"{section}.{key} must be a valid IPv4/IPv6 address")


def _require_positive_int(errors: list[str], section: str, key: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"{section}.{key} must be a positive integer")


def _require_nonnegative_int(
    errors: list[str], section: str, key: str, value: Any
) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{section}.{key} must be a non-negative integer")


def _require_positive_float(errors: list[str], section: str, key: str, value: Any) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        errors.append(f"{section}.{key} must be a positive finite number")


def _require_vector3(errors: list[str], section: str, key: str, value: Any) -> None:
    if not isinstance(value, list) or len(value) != 3:
        errors.append(f"{section}.{key} must be a list of three numbers")
        return
    for item in value:
        if (
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(item)
        ):
            errors.append(f"{section}.{key} must contain finite numeric values only")
            return


def normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap ROS 2 ``ros__parameters`` blocks into flat section mappings."""
    normalized: dict[str, Any] = {}
    for section, value in data.items():
        if isinstance(value, dict) and "ros__parameters" in value:
            params = value["ros__parameters"]
            normalized[section] = params if isinstance(params, dict) else {}
        elif isinstance(value, dict):
            normalized[section] = value
        else:
            normalized[section] = value
    return normalized


def validate_config(data: dict[str, Any]) -> list[str]:
    """Return validation errors for ``data``; empty list means valid."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["root document must be a mapping"]

    data = normalize_config(data)
    for section in REQUIRED_SECTIONS:
        if section not in data:
            errors.append(f"missing required section: {section}")

    bringup = data.get("scout_bringup", {})
    if isinstance(bringup, dict):
        _require_string(errors, "scout_bringup", "can_interface", bringup.get("can_interface"))
        if bringup.get("can_interface") != "can_scout":
            errors.append("scout_bringup.can_interface must be 'can_scout'")
        _require_positive_int(
            errors,
            "scout_bringup",
            "can_bitrate",
            bringup.get("can_bitrate"),
        )
        if bringup.get("can_bitrate") != 500_000:
            errors.append("scout_bringup.can_bitrate must be 500000")
        _require_nonnegative_int(
            errors,
            "scout_bringup",
            "can_restart_ms",
            bringup.get("can_restart_ms"),
        )
        if bringup.get("can_restart_ms") != 100:
            errors.append("scout_bringup.can_restart_ms must be 100")
        _require_string(
            errors,
            "scout_bringup",
            "sensor_interface",
            bringup.get("sensor_interface"),
        )
        _require_topic(
            errors,
            "scout_bringup",
            "cmd_vel_input_topic",
            bringup.get("cmd_vel_input_topic"),
        )
        _require_ip(errors, "scout_bringup", "host_ip", bringup.get("host_ip"))
        _require_ip(errors, "scout_bringup", "lidar_ip", bringup.get("lidar_ip"))
        _require_ip(errors, "scout_bringup", "camera_ip", bringup.get("camera_ip"))
        _require_frame(errors, "scout_bringup", "base_frame", bringup.get("base_frame"))
        _require_frame(errors, "scout_bringup", "body_frame", bringup.get("body_frame"))
        _require_frame(errors, "scout_bringup", "lidar_frame", bringup.get("lidar_frame"))
        _require_frame(errors, "scout_bringup", "camera_frame", bringup.get("camera_frame"))
        _require_frame(
            errors,
            "scout_bringup",
            "camera_optical_frame",
            bringup.get("camera_optical_frame"),
        )
        if bringup.get("camera_frame") == bringup.get("camera_optical_frame"):
            errors.append(
                "scout_bringup.camera_frame must differ from camera_optical_frame"
            )
        if "extrinsics_verified" in bringup and not isinstance(
            bringup["extrinsics_verified"], bool
        ):
            errors.append("scout_bringup.extrinsics_verified must be a boolean")
        _require_vector3(
            errors,
            "scout_bringup",
            "base_to_body_xyz_m",
            bringup.get("base_to_body_xyz_m"),
        )
        _require_vector3(
            errors,
            "scout_bringup",
            "base_to_body_rpy_rad",
            bringup.get("base_to_body_rpy_rad"),
        )
        _require_vector3(
            errors,
            "scout_bringup",
            "base_to_lidar_xyz_m",
            bringup.get("base_to_lidar_xyz_m"),
        )
        _require_vector3(
            errors,
            "scout_bringup",
            "base_to_lidar_rpy_rad",
            bringup.get("base_to_lidar_rpy_rad"),
        )
        _require_vector3(
            errors,
            "scout_bringup",
            "base_to_camera_xyz_m",
            bringup.get("base_to_camera_xyz_m"),
        )
        _require_vector3(
            errors,
            "scout_bringup",
            "base_to_camera_rpy_rad",
            bringup.get("base_to_camera_rpy_rad"),
        )

    camera = data.get("hik_camera_node", {})
    if isinstance(camera, dict):
        backend = camera.get("backend")
        _require_string(errors, "hik_camera_node", "backend", backend)
        if isinstance(backend, str) and backend not in ALLOWED_BACKENDS:
            errors.append(f"hik_camera_node.backend must be one of {sorted(ALLOWED_BACKENDS)}")
        _require_ip(errors, "hik_camera_node", "host_ip", camera.get("host_ip"))
        _require_ip(errors, "hik_camera_node", "camera_ip", camera.get("camera_ip"))
        _require_topic(errors, "hik_camera_node", "image_topic", camera.get("image_topic"))
        _require_topic(
            errors,
            "hik_camera_node",
            "camera_info_topic",
            camera.get("camera_info_topic"),
        )
        _require_frame(errors, "hik_camera_node", "frame_id", camera.get("frame_id"))
        camera_name = camera.get("camera_name")
        _require_string(errors, "hik_camera_node", "camera_name", camera_name)
        if (
            isinstance(camera_name, str)
            and camera_name
            and CAMERA_NAME_PATTERN.fullmatch(camera_name) is None
        ):
            errors.append(
                "hik_camera_node.camera_name may contain only letters, numbers, and underscores"
            )
        _require_string(
            errors,
            "hik_camera_node",
            "camera_model",
            camera.get("camera_model"),
        )
        _require_string(
            errors,
            "hik_camera_node",
            "camera_serial",
            camera.get("camera_serial"),
            allow_empty=True,
        )
        _require_string(
            errors,
            "hik_camera_node",
            "lens_id",
            camera.get("lens_id"),
            allow_empty=True,
        )
        calibration_url = camera.get("calibration_url")
        if not isinstance(calibration_url, str):
            errors.append("hik_camera_node.calibration_url must be a string")
        elif calibration_url and CALIBRATION_URL_PATTERN.fullmatch(calibration_url) is None:
            errors.append(
                "hik_camera_node.calibration_url must use file:// or package://"
            )
        if isinstance(bringup, dict) and (
            camera.get("frame_id") != bringup.get("camera_optical_frame")
        ):
            errors.append(
                "hik_camera_node.frame_id must match "
                "scout_bringup.camera_optical_frame"
            )
        pixel_format = camera.get("pixel_format")
        _require_string(errors, "hik_camera_node", "pixel_format", pixel_format)
        if isinstance(pixel_format, str) and pixel_format not in ALLOWED_PIXEL_FORMATS:
            errors.append(
                "hik_camera_node.pixel_format must be one of "
                f"{sorted(ALLOWED_PIXEL_FORMATS)}"
            )
        width = camera.get("width")
        height = camera.get("height")
        sensor_width = camera.get("sensor_width")
        sensor_height = camera.get("sensor_height")
        _require_positive_int(
            errors,
            "hik_camera_node",
            "sensor_width",
            sensor_width,
        )
        _require_positive_int(
            errors,
            "hik_camera_node",
            "sensor_height",
            sensor_height,
        )
        if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
            errors.append("hik_camera_node.width must be a positive integer")
        if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
            errors.append("hik_camera_node.height must be a positive integer")
        _require_nonnegative_int(
            errors,
            "hik_camera_node",
            "offset_x",
            camera.get("offset_x"),
        )
        _require_nonnegative_int(
            errors,
            "hik_camera_node",
            "offset_y",
            camera.get("offset_y"),
        )
        offset_x = camera.get("offset_x")
        offset_y = camera.get("offset_y")
        if all(
            isinstance(item, int) and not isinstance(item, bool)
            for item in (width, height, offset_x, offset_y, sensor_width, sensor_height)
        ):
            if offset_x + width > sensor_width:
                errors.append("hik_camera_node horizontal ROI exceeds sensor_width")
            if offset_y + height > sensor_height:
                errors.append("hik_camera_node vertical ROI exceeds sensor_height")
        _require_positive_float(
            errors,
            "hik_camera_node",
            "frame_rate",
            camera.get("frame_rate"),
        )
        for key in ("exposure_auto", "gain_auto"):
            if not isinstance(camera.get(key), bool):
                errors.append(f"hik_camera_node.{key} must be a boolean")
        _require_positive_float(
            errors,
            "hik_camera_node",
            "exposure_time_us",
            camera.get("exposure_time_us"),
        )
        gain_db = camera.get("gain_db")
        if (
            not isinstance(gain_db, (int, float))
            or isinstance(gain_db, bool)
            or not math.isfinite(gain_db)
            or gain_db < 0
        ):
            errors.append("hik_camera_node.gain_db must be a non-negative finite number")
        _require_positive_int(
            errors,
            "hik_camera_node",
            "reconnect_interval_ms",
            camera.get("reconnect_interval_ms"),
        )
        _require_topic(
            errors,
            "hik_camera_node",
            "diagnostics_topic",
            camera.get("diagnostics_topic"),
        )

    safety = data.get("safety_mux", {})
    if isinstance(safety, dict):
        for key in (
            "teleop_topic",
            "auto_topic",
            "guarded_topic",
            "output_topic",
            "diagnostics_topic",
            "joy_topic",
            "emergency_stop_topic",
            "chassis_fault_topic",
            "lidar_healthy_topic",
            "tf_healthy_topic",
            "obstacle_clear_topic",
        ):
            _require_topic(errors, "safety_mux", key, safety.get(key))
        _require_positive_int(
            errors, "safety_mux", "command_timeout_ms", safety.get("command_timeout_ms")
        )
        _require_positive_int(
            errors, "safety_mux", "health_timeout_ms", safety.get("health_timeout_ms")
        )
        _require_positive_float(
            errors, "safety_mux", "max_linear_mps", safety.get("max_linear_mps")
        )
        if safety.get("max_linear_mps") != 0.2:
            errors.append("safety_mux.max_linear_mps must be 0.2")
        _require_positive_float(
            errors, "safety_mux", "max_angular_rps", safety.get("max_angular_rps")
        )
        if not isinstance(safety.get("require_obstacle_clear"), bool):
            errors.append("safety_mux.require_obstacle_clear must be a boolean")
        if safety.get("require_hold_to_run") is not True:
            errors.append("safety_mux.require_hold_to_run must be true")
        _require_nonnegative_int(
            errors,
            "safety_mux",
            "teleop_enable_button",
            safety.get("teleop_enable_button"),
        )
        if not isinstance(safety.get("require_emergency_stop_heartbeat"), bool):
            errors.append(
                "safety_mux.require_emergency_stop_heartbeat must be a boolean"
            )
        if isinstance(bringup, dict) and (
            bringup.get("cmd_vel_input_topic") != safety.get("output_topic")
        ):
            errors.append(
                "scout_bringup.cmd_vel_input_topic must match safety_mux.output_topic"
            )

    collision = data.get("collision_monitor", {})
    if isinstance(collision, dict):
        for key in ("input_topic", "obstacle_clear_topic", "diagnostics_topic"):
            _require_topic(errors, "collision_monitor", key, collision.get(key))
        _require_string(
            errors,
            "collision_monitor",
            "expected_frame_id",
            collision.get("expected_frame_id"),
        )
        _require_positive_int(
            errors,
            "collision_monitor",
            "cloud_timeout_ms",
            collision.get("cloud_timeout_ms"),
        )
        _require_positive_int(
            errors,
            "collision_monitor",
            "min_points",
            collision.get("min_points"),
        )
        _require_positive_int(
            errors,
            "collision_monitor",
            "clear_confirmations",
            collision.get("clear_confirmations"),
        )
        for key in ("min_x_m", "max_x_m", "half_width_m"):
            _require_positive_float(errors, "collision_monitor", key, collision.get(key))
        min_x = collision.get("min_x_m")
        max_x = collision.get("max_x_m")
        if (
            isinstance(min_x, (int, float))
            and isinstance(max_x, (int, float))
            and min_x >= max_x
        ):
            errors.append("collision_monitor.min_x_m must be less than max_x_m")
        min_z = collision.get("min_z_m")
        max_z = collision.get("max_z_m")
        min_z_valid = (
            isinstance(min_z, (int, float))
            and not isinstance(min_z, bool)
            and math.isfinite(min_z)
        )
        max_z_valid = (
            isinstance(max_z, (int, float))
            and not isinstance(max_z, bool)
            and math.isfinite(max_z)
        )
        if not min_z_valid:
            errors.append("collision_monitor.min_z_m must be a finite number")
        if not max_z_valid:
            errors.append("collision_monitor.max_z_m must be a finite number")
        if (
            min_z_valid
            and max_z_valid
            and min_z >= max_z
        ):
            errors.append("collision_monitor.min_z_m must be less than max_z_m")

    route = data.get("route_orchestrator", {})
    if isinstance(route, dict):
        fixed_route_path = route.get("fixed_route_path")
        _require_string(
            errors,
            "route_orchestrator",
            "fixed_route_path",
            fixed_route_path,
            allow_empty=True,
        )
        if isinstance(fixed_route_path, str) and fixed_route_path and not Path(
            fixed_route_path
        ).is_absolute():
            errors.append("route_orchestrator.fixed_route_path must be absolute")
        for key in (
            "controller_config",
            "expected_auto_publisher_node",
            "controller_id",
            "goal_checker_id",
        ):
            _require_string(errors, "route_orchestrator", key, route.get(key))
        for key in (
            "route_status_topic",
            "safety_diagnostics_topic",
            "inspection_status_topic",
            "auto_command_topic",
            "follow_path_action",
        ):
            _require_topic(errors, "route_orchestrator", key, route.get(key))
        if not isinstance(route.get("controller_tuning_verified"), bool):
            errors.append(
                "route_orchestrator.controller_tuning_verified must be a boolean"
            )
        for key in (
            "first_command_timeout_ms",
            "safety_diagnostic_timeout_ms",
            "inspection_status_timeout_ms",
        ):
            _require_positive_int(errors, "route_orchestrator", key, route.get(key))
        if isinstance(safety, dict):
            if route.get("auto_command_topic") != safety.get("auto_topic"):
                errors.append(
                    "route_orchestrator.auto_command_topic must match safety_mux.auto_topic"
                )
            if route.get("safety_diagnostics_topic") != safety.get(
                "diagnostics_topic"
            ):
                errors.append(
                    "route_orchestrator.safety_diagnostics_topic must match "
                    "safety_mux.diagnostics_topic"
                )

    inspection = data.get("inspection_manager", {})
    if isinstance(inspection, dict):
        run_mode = inspection.get("run_mode")
        _require_string(errors, "inspection_manager", "run_mode", run_mode)
        if isinstance(run_mode, str) and run_mode not in RUN_MODES:
            errors.append(
                f"inspection_manager.run_mode must be one of {sorted(RUN_MODES)}"
            )
        _require_string(errors, "inspection_manager", "output_root", inspection.get("output_root"))
        storage_id = inspection.get("storage_id")
        _require_string(errors, "inspection_manager", "storage_id", storage_id)
        if isinstance(storage_id, str) and storage_id != "mcap":
            errors.append("inspection_manager.storage_id must be 'mcap'")
        _require_topic(
            errors,
            "inspection_manager",
            "status_topic",
            inspection.get("status_topic"),
        )
        _require_topic(
            errors,
            "inspection_manager",
            "route_status_topic",
            inspection.get("route_status_topic"),
        )
        _require_topic(
            errors,
            "inspection_manager",
            "diagnostics_topic",
            inspection.get("diagnostics_topic"),
        )
        _require_string(
            errors,
            "inspection_manager",
            "camera_diagnostic_name",
            inspection.get("camera_diagnostic_name"),
        )
        _require_positive_int(
            errors,
            "inspection_manager",
            "camera_diagnostic_timeout_ms",
            inspection.get("camera_diagnostic_timeout_ms"),
        )
        target_url = inspection.get("calibration_target_url")
        if not isinstance(target_url, str):
            errors.append(
                "inspection_manager.calibration_target_url must be a string"
            )
        elif target_url and CALIBRATION_URL_PATTERN.fullmatch(target_url) is None:
            errors.append(
                "inspection_manager.calibration_target_url must use file:// or package://"
            )
        profile_url = inspection.get("calibration_profile_url")
        if not isinstance(profile_url, str):
            errors.append(
                "inspection_manager.calibration_profile_url must be a string"
            )
        elif profile_url and CALIBRATION_URL_PATTERN.fullmatch(profile_url) is None:
            errors.append(
                "inspection_manager.calibration_profile_url must use file:// or package://"
            )
        camera_url = camera.get("calibration_url") if isinstance(camera, dict) else None
        configured_calibration_inputs = {
            bool(camera_url),
            bool(target_url),
            bool(profile_url),
        }
        if len(configured_calibration_inputs) != 1:
            errors.append(
                "camera calibration, target, and profile URLs must be configured together"
            )
        if run_mode == "production":
            if not camera_url:
                errors.append(
                    "inspection_manager.run_mode=production requires camera calibration"
                )
            if isinstance(camera, dict) and camera.get("backend") != "mvs":
                errors.append(
                    "inspection_manager.run_mode=production requires hik_camera_node.backend=mvs"
                )
            if not isinstance(camera, dict) or not camera.get("camera_serial"):
                errors.append(
                    "inspection_manager.run_mode=production requires a camera serial"
                )
            if not isinstance(camera, dict) or not camera.get("lens_id"):
                errors.append(
                    "inspection_manager.run_mode=production requires a lens_id"
                )
            if not isinstance(bringup, dict) or not bringup.get(
                "extrinsics_verified", False
            ):
                errors.append(
                    "inspection_manager.run_mode=production requires "
                    "scout_bringup.extrinsics_verified=true"
                )
        if "recording_config" in inspection and not isinstance(
            inspection["recording_config"], str
        ):
            errors.append("inspection_manager.recording_config must be a string")
        if "config_path" in inspection and not isinstance(inspection["config_path"], str):
            errors.append("inspection_manager.config_path must be a string")
        if isinstance(route, dict) and (
            route.get("inspection_status_topic") != inspection.get("status_topic")
        ):
            errors.append(
                "route_orchestrator.inspection_status_topic must match "
                "inspection_manager.status_topic"
            )
        if isinstance(route, dict) and (
            route.get("route_status_topic") != inspection.get("route_status_topic")
        ):
            errors.append(
                "route_orchestrator.route_status_topic must match "
                "inspection_manager.route_status_topic"
            )

    health = data.get("health_monitor", {})
    if health:
        if not isinstance(health, dict):
            errors.append("health_monitor must be a mapping")
        else:
            _require_topic(errors, "health_monitor", "lidar_topic", health.get("lidar_topic"))
            for key in (
                "emergency_stop_topic",
                "chassis_fault_topic",
                "lidar_healthy_topic",
                "tf_healthy_topic",
                "odom_tf_healthy_topic",
                "scout_status_topic",
                "scout_rc_topic",
                "scout_odom_topic",
                "diagnostics_topic",
            ):
                _require_topic(errors, "health_monitor", key, health.get(key))
            _require_frame(
                errors,
                "health_monitor",
                "tf_source_frame",
                health.get("tf_source_frame"),
            )
            _require_frame(
                errors,
                "health_monitor",
                "tf_target_frame",
                health.get("tf_target_frame"),
            )
            for key in (
                "lidar_timeout_ms",
                "tf_timeout_ms",
                "chassis_timeout_ms",
                "can_rx_timeout_ms",
                "emergency_stop_timeout_ms",
            ):
                _require_positive_int(
                    errors,
                    "health_monitor",
                    key,
                    health.get(key),
                )
            for key in (
                "monitor_lidar",
                "monitor_tf",
                "monitor_chassis",
                "monitor_can_rx",
                "monitor_emergency_stop",
            ):
                if not isinstance(health.get(key), bool):
                    errors.append(f"health_monitor.{key} must be a boolean")
            if health.get("monitor_can_rx") is not True:
                errors.append("health_monitor.monitor_can_rx must be true")
            _require_string(
                errors,
                "health_monitor",
                "can_interface",
                health.get("can_interface"),
            )
            if isinstance(bringup, dict) and (
                health.get("can_interface") != bringup.get("can_interface")
            ):
                errors.append(
                    "health_monitor.can_interface must match "
                    "scout_bringup.can_interface"
                )
            _require_nonnegative_int(
                errors,
                "health_monitor",
                "expected_control_mode",
                health.get("expected_control_mode"),
            )
            _require_nonnegative_int(
                errors,
                "health_monitor",
                "expected_vehicle_state",
                health.get("expected_vehicle_state"),
            )
            if isinstance(safety, dict) and (
                health.get("monitor_emergency_stop")
                != safety.get("require_emergency_stop_heartbeat")
            ):
                errors.append(
                    "health_monitor.monitor_emergency_stop must match "
                    "safety_mux.require_emergency_stop_heartbeat"
                )
            if isinstance(safety, dict) and (
                health.get("tf_healthy_topic") != safety.get("tf_healthy_topic")
            ):
                errors.append(
                    "health_monitor.tf_healthy_topic must match "
                    "safety_mux.tf_healthy_topic"
                )
            if health.get("odom_tf_healthy_topic") == health.get(
                "tf_healthy_topic"
            ):
                errors.append(
                    "health_monitor.odom_tf_healthy_topic must differ from "
                    "tf_healthy_topic"
                )

    localization = data.get("localization_adapter", {})
    if isinstance(localization, dict):
        for key in ("input_odom_topic", "health_topic", "diagnostics_topic"):
            _require_topic(
                errors,
                "localization_adapter",
                key,
                localization.get(key),
            )
        for key in (
            "input_global_frame",
            "body_frame",
            "map_frame",
            "odom_frame",
            "base_frame",
        ):
            _require_frame(
                errors,
                "localization_adapter",
                key,
                localization.get(key),
            )
        for key in (
            "pose_timeout_ms",
            "tf_lookup_timeout_ms",
            "future_tolerance_ms",
            "healthy_confirmations",
        ):
            _require_positive_int(
                errors,
                "localization_adapter",
                key,
                localization.get(key),
            )
        for key in ("max_translation_jump_m", "max_rotation_jump_rad"):
            _require_positive_float(
                errors,
                "localization_adapter",
                key,
                localization.get(key),
            )
        if isinstance(bringup, dict):
            for localization_key, bringup_key in (
                ("base_frame", "base_frame"),
                ("body_frame", "body_frame"),
            ):
                if localization.get(localization_key) != bringup.get(bringup_key):
                    errors.append(
                        f"localization_adapter.{localization_key} must match "
                        f"scout_bringup.{bringup_key}"
                    )
        if isinstance(health, dict):
            for localization_key, health_key in (
                ("odom_frame", "tf_source_frame"),
                ("base_frame", "tf_target_frame"),
            ):
                if localization.get(localization_key) != health.get(health_key):
                    errors.append(
                        f"localization_adapter.{localization_key} must match "
                        f"health_monitor.{health_key}"
                    )
        if isinstance(safety, dict) and (
            localization.get("health_topic") != safety.get("tf_healthy_topic")
        ):
            errors.append(
                "localization_adapter.health_topic must match safety_mux.tf_healthy_topic"
            )
        if localization.get("map_frame") == localization.get("odom_frame"):
            errors.append(
                "localization_adapter.map_frame must differ from odom_frame"
            )

    joy = data.get("joy_node", {})
    if isinstance(joy, dict):
        _require_nonnegative_int(errors, "joy_node", "device_id", joy.get("device_id"))
        _require_positive_float(errors, "joy_node", "deadzone", joy.get("deadzone"))
        _require_positive_float(
            errors,
            "joy_node",
            "autorepeat_rate",
            joy.get("autorepeat_rate"),
        )
        _require_nonnegative_int(
            errors,
            "joy_node",
            "coalesce_interval_ms",
            joy.get("coalesce_interval_ms"),
        )

    teleop = data.get("teleop_twist_joy", {})
    if isinstance(teleop, dict):
        if teleop.get("require_enable_button") is not True:
            errors.append("teleop_twist_joy.require_enable_button must be true")
        _require_nonnegative_int(
            errors,
            "teleop_twist_joy",
            "enable_button",
            teleop.get("enable_button"),
        )
        for key in ("axis_linear.x", "axis_angular.yaw"):
            _require_nonnegative_int(errors, "teleop_twist_joy", key, teleop.get(key))
        for key in (
            "scale_linear.x",
            "scale_linear_turbo.x",
            "scale_angular.yaw",
            "scale_angular_turbo.yaw",
        ):
            _require_positive_float(errors, "teleop_twist_joy", key, teleop.get(key))
        if isinstance(safety, dict):
            if safety.get("teleop_enable_button") != teleop.get("enable_button"):
                errors.append(
                    "safety_mux.teleop_enable_button must match "
                    "teleop_twist_joy.enable_button"
                )
            linear_limit = safety.get("max_linear_mps")
            angular_limit = safety.get("max_angular_rps")
            for key in ("scale_linear.x", "scale_linear_turbo.x"):
                value = teleop.get(key)
                if (
                    isinstance(value, (int, float))
                    and isinstance(linear_limit, (int, float))
                    and value > linear_limit
                ):
                    errors.append(
                        f"teleop_twist_joy.{key} must not exceed "
                        "safety_mux.max_linear_mps"
                    )
            for key in ("scale_angular.yaw", "scale_angular_turbo.yaw"):
                value = teleop.get(key)
                if (
                    isinstance(value, (int, float))
                    and isinstance(angular_limit, (int, float))
                    and value > angular_limit
                ):
                    errors.append(
                        f"teleop_twist_joy.{key} must not exceed "
                        "safety_mux.max_angular_rps"
                    )

    return errors


def load_config(path: Path) -> dict[str, Any]:
    """Load YAML config from ``path``."""
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the root")
    return normalize_config(loaded)


def validate_config_file(path: Path) -> list[str]:
    """Load and validate the config file at ``path``."""
    try:
        data = load_config(path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        return [f"failed to load {path}: {exc}"]
    return validate_config(data)


def main() -> None:
    """CLI entry point for config validation."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Validate scout_bringup system.yaml")
    parser.add_argument(
        "config",
        nargs="?",
        type=Path,
        help="Path to system.yaml (defaults to package share config)",
    )
    args = parser.parse_args()

    if args.config is None:
        from ament_index_python.packages import get_package_share_directory

        share = Path(get_package_share_directory("scout_bringup"))
        config_path = share / "config" / "system.yaml"
    else:
        config_path = args.config

    errors = validate_config_file(config_path)
    if errors:
        for item in errors:
            print(f"ERROR {item}", file=sys.stderr)
        raise SystemExit(1)

    print(f"OK   config: {config_path}")


if __name__ == "__main__":
    main()
