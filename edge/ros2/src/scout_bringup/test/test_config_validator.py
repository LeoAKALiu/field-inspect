"""Tests for system.yaml validation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scout_bringup.config_validator import load_config, validate_config, validate_config_file


@pytest.fixture
def valid_config() -> dict:
    """Return a minimal valid configuration mapping."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    return load_config(config_path)


def test_default_system_yaml_is_valid() -> None:
    """The shipped system.yaml should pass validation."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    assert validate_config_file(config_path) == []


def test_default_system_yaml_uses_stable_can_and_hold_to_run() -> None:
    """The shipped motion contract must use the verified guarded defaults."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    assert config["scout_bringup"]["can_interface"] == "can_scout"
    assert config["scout_bringup"]["can_bitrate"] == 500_000
    assert config["safety_mux"]["require_hold_to_run"] is True
    assert config["teleop_twist_joy"]["require_enable_button"] is True
    assert (
        config["safety_mux"]["teleop_enable_button"]
        == config["teleop_twist_joy"]["enable_button"]
    )
    assert config["health_monitor"]["monitor_emergency_stop"] is False
    assert config["health_monitor"]["monitor_can_rx"] is True
    assert config["health_monitor"]["can_interface"] == "can_scout"
    assert config["safety_mux"]["require_emergency_stop_heartbeat"] is False
    assert config["inspection_manager"]["run_mode"] == "commissioning"
    assert config["hik_camera_node"]["frame_id"] == "camera_optical_frame"
    assert config["hik_camera_node"]["camera_model"] == "MV-CS050-10GC"
    assert config["hik_camera_node"]["sensor_width"] == 2448
    assert config["hik_camera_node"]["sensor_height"] == 2048
    assert config["hik_camera_node"]["offset_x"] == 584
    assert config["hik_camera_node"]["offset_y"] == 512
    assert config["hik_camera_node"]["exposure_auto"] is False
    assert config["hik_camera_node"]["exposure_time_us"] == 50_000.0
    assert config["hik_camera_node"]["gain_auto"] is False
    assert config["hik_camera_node"]["gain_db"] == 0.0


def test_default_system_yaml_uses_verified_extrinsics() -> None:
    """The shipped frame chain must retain the accepted 2026-09-06 calibration."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)
    bringup = config["scout_bringup"]

    assert bringup["extrinsics_verified"] is True
    assert bringup["base_to_body_xyz_m"] == [0.04103355, 0.02872873, 0.37200011]
    assert bringup["base_to_body_rpy_rad"] == [
        -0.00202000,
        -0.01475178,
        0.04758438,
    ]
    assert bringup["base_to_lidar_xyz_m"] == [0.0305, 0.005, 0.416]
    assert bringup["base_to_lidar_rpy_rad"] == [
        -0.00202000,
        -0.01475178,
        0.04758438,
    ]
    assert bringup["base_to_camera_xyz_m"] == [
        0.22124407,
        -0.00733363,
        0.34426372,
    ]
    assert bringup["base_to_camera_rpy_rad"] == [
        -0.02649034,
        0.00505634,
        0.01443088,
    ]


def test_production_mode_requires_calibration_and_verified_extrinsics(
    valid_config: dict,
) -> None:
    broken = deepcopy(valid_config)
    broken["inspection_manager"]["run_mode"] = "production"
    broken["hik_camera_node"]["calibration_url"] = ""
    broken["inspection_manager"]["calibration_target_url"] = ""
    broken["inspection_manager"]["calibration_profile_url"] = ""
    broken["scout_bringup"]["extrinsics_verified"] = False

    errors = validate_config(broken)

    assert (
        "inspection_manager.run_mode=production requires camera calibration"
        in errors
    )
    assert (
        "inspection_manager.run_mode=production requires "
        "scout_bringup.extrinsics_verified=true"
    ) in errors


def test_production_mode_accepts_paired_versioned_calibration(
    valid_config: dict,
) -> None:
    configured = deepcopy(valid_config)
    configured["inspection_manager"]["run_mode"] = "production"
    configured["inspection_manager"]["calibration_target_url"] = (
        "file:///data/scout_calibration/target-v1.yaml"
    )
    configured["inspection_manager"]["calibration_profile_url"] = (
        "file:///data/scout_calibration/camera-v1.profile.yaml"
    )
    configured["hik_camera_node"]["calibration_url"] = (
        "file:///data/scout_calibration/camera-v1.yaml"
    )
    configured["hik_camera_node"]["camera_serial"] = "DA1234567"
    configured["hik_camera_node"]["lens_id"] = "lens-01-focus-locked-v1"
    configured["scout_bringup"]["extrinsics_verified"] = True

    assert validate_config(configured) == []


def test_camera_frame_must_use_the_optical_frame(valid_config: dict) -> None:
    broken = deepcopy(valid_config)
    broken["hik_camera_node"]["frame_id"] = "camera_link"

    errors = validate_config(broken)

    assert (
        "hik_camera_node.frame_id must match scout_bringup.camera_optical_frame"
        in errors
    )


def test_nav2_subset_keeps_rpp_behind_the_project_safety_boundary() -> None:
    """The committed seed must not enable planners or a competing collision authority."""
    config_path = Path(__file__).resolve().parents[1] / "config/nav2_controller.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    controller = config["controller_server"]["ros__parameters"]
    costmap = config["local_costmap"]["local_costmap"]["ros__parameters"]
    lifecycle = config["lifecycle_manager_route"]["ros__parameters"]

    assert controller["controller_plugins"] == ["FollowPath"]
    assert controller["FollowPath"]["plugin"].endswith(
        "RegulatedPurePursuitController"
    )
    assert controller["FollowPath"]["desired_linear_vel"] == 0.2
    assert controller["FollowPath"]["use_collision_detection"] is False
    assert lifecycle["node_names"] == ["controller_server"]
    assert isinstance(costmap["width"], int)
    assert isinstance(costmap["height"], int)
    assert costmap["plugins"] == ["inflation_layer"]


def test_validate_config_rejects_missing_section(valid_config: dict) -> None:
    """Missing top-level sections must be reported."""
    broken = deepcopy(valid_config)
    del broken["safety_mux"]
    errors = validate_config(broken)
    assert any("missing required section: safety_mux" in item for item in errors)


def test_validate_config_rejects_invalid_ip(valid_config: dict) -> None:
    """Invalid IP addresses must be rejected."""
    broken = deepcopy(valid_config)
    broken["scout_bringup"]["camera_ip"] = "not-an-ip"
    errors = validate_config(broken)
    assert any("camera_ip" in item for item in errors)


def test_validate_config_rejects_invalid_backend(valid_config: dict) -> None:
    """Camera backend must stay within the supported set."""
    broken = deepcopy(valid_config)
    broken["hik_camera_node"]["backend"] = "unsupported"
    errors = validate_config(broken)
    assert any("backend must be one of" in item for item in errors)


def test_validate_config_rejects_unsupported_camera_pixel_format(
    valid_config: dict,
) -> None:
    """The configured encoding must match what the camera bridge publishes."""
    broken = deepcopy(valid_config)
    broken["hik_camera_node"]["pixel_format"] = "Mono8"

    errors = validate_config(broken)

    assert any("pixel_format must be one of" in item for item in errors)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("frame_rate", 0.0, "frame_rate must be a positive finite number"),
        ("exposure_auto", 1, "exposure_auto must be a boolean"),
        ("exposure_time_us", 0.0, "exposure_time_us must be a positive finite number"),
        ("gain_auto", "false", "gain_auto must be a boolean"),
        ("gain_db", -0.1, "gain_db must be a non-negative finite number"),
    ],
)
def test_validate_config_rejects_invalid_camera_acquisition_setting(
    valid_config: dict,
    key: str,
    value: object,
    message: str,
) -> None:
    broken = deepcopy(valid_config)
    broken["hik_camera_node"][key] = value

    assert any(message in error for error in validate_config(broken))


@pytest.mark.parametrize("key", ["offset_x", "offset_y"])
def test_validate_config_rejects_negative_camera_roi_offset(
    valid_config: dict,
    key: str,
) -> None:
    """A sensor ROI cannot start outside the nonnegative sensor coordinate space."""
    broken = deepcopy(valid_config)
    broken["hik_camera_node"][key] = -4

    errors = validate_config(broken)

    assert any(
        f"hik_camera_node.{key} must be a non-negative integer" in item
        for item in errors
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("offset_x", 1200, "horizontal ROI exceeds sensor_width"),
        ("offset_y", 1100, "vertical ROI exceeds sensor_height"),
    ],
)
def test_validate_config_rejects_camera_roi_outside_sensor(
    valid_config: dict,
    key: str,
    value: int,
    message: str,
) -> None:
    broken = deepcopy(valid_config)
    broken["hik_camera_node"][key] = value

    assert any(message in error for error in validate_config(broken))


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("camera_serial", "requires a camera serial"),
        ("lens_id", "requires a lens_id"),
    ],
)
def test_production_rejects_missing_camera_acquisition_identity(
    valid_config: dict,
    key: str,
    message: str,
) -> None:
    configured = deepcopy(valid_config)
    configured["inspection_manager"]["run_mode"] = "production"
    configured["inspection_manager"]["calibration_target_url"] = (
        "file:///data/scout_calibration/target-v1.yaml"
    )
    configured["inspection_manager"]["calibration_profile_url"] = (
        "file:///data/scout_calibration/camera-v1.profile.yaml"
    )
    configured["hik_camera_node"]["calibration_url"] = (
        "file:///data/scout_calibration/camera-v1.yaml"
    )
    configured["hik_camera_node"]["camera_serial"] = "DA1234567"
    configured["hik_camera_node"]["lens_id"] = "lens-01-focus-locked-v1"
    configured["hik_camera_node"][key] = ""
    configured["scout_bringup"]["extrinsics_verified"] = True

    assert any(message in error for error in validate_config(configured))


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("camera_name", "camera/front", "camera_name may contain only"),
        ("calibration_url", "https://example.invalid/camera.yaml", "must use file://"),
    ],
)
def test_validate_config_rejects_invalid_camera_calibration_identity(
    valid_config: dict,
    key: str,
    value: str,
    message: str,
) -> None:
    """Calibration inputs must be explicit and locally resolvable."""
    broken = deepcopy(valid_config)
    broken["hik_camera_node"][key] = value

    errors = validate_config(broken)

    assert any(message in error for error in errors)


def test_validate_config_rejects_bad_topic(valid_config: dict) -> None:
    """Topic names must be absolute paths."""
    broken = deepcopy(valid_config)
    broken["safety_mux"]["output_topic"] = "cmd_vel_safe"
    errors = validate_config(broken)
    assert any("output_topic must be an absolute ROS topic path" in item for item in errors)


def test_validate_config_rejects_localization_frame_or_health_drift(
    valid_config: dict,
) -> None:
    """The adapter must preserve the configured TF and safety ownership contract."""
    broken = deepcopy(valid_config)
    broken["localization_adapter"]["body_frame"] = "imu_link"
    broken["localization_adapter"]["health_topic"] = "/localization/healthy"

    errors = validate_config(broken)

    assert (
        "localization_adapter.body_frame must match scout_bringup.body_frame"
        in errors
    )
    assert (
        "localization_adapter.health_topic must match safety_mux.tf_healthy_topic"
        in errors
    )


def test_validate_config_rejects_localization_ownership_drift(
    valid_config: dict,
) -> None:
    """Internal and final TF health publishers must remain unambiguous."""
    broken = deepcopy(valid_config)
    broken["health_monitor"]["tf_healthy_topic"] = "/wrong/final_health"
    broken["health_monitor"]["odom_tf_healthy_topic"] = "/wrong/final_health"
    broken["health_monitor"]["tf_source_frame"] = "wheel_odom"
    broken["localization_adapter"]["map_frame"] = "odom"

    errors = validate_config(broken)

    assert (
        "health_monitor.tf_healthy_topic must match safety_mux.tf_healthy_topic"
        in errors
    )
    assert (
        "health_monitor.odom_tf_healthy_topic must differ from tf_healthy_topic"
        in errors
    )
    assert (
        "localization_adapter.odom_frame must match health_monitor.tf_source_frame"
        in errors
    )
    assert "localization_adapter.map_frame must differ from odom_frame" in errors


def test_validate_config_rejects_nonfinite_body_extrinsic(
    valid_config: dict,
) -> None:
    """A NaN static transform must never enter the authoritative TF tree."""
    broken = deepcopy(valid_config)
    broken["scout_bringup"]["base_to_body_xyz_m"][0] = float("nan")

    errors = validate_config(broken)

    assert (
        "scout_bringup.base_to_body_xyz_m must contain finite numeric values only"
        in errors
    )


def test_validate_config_rejects_chassis_bypass_topic(valid_config: dict) -> None:
    """The base driver must consume exactly the safety mux output."""
    broken = deepcopy(valid_config)
    broken["scout_bringup"]["cmd_vel_input_topic"] = "/cmd_vel_teleop"

    errors = validate_config(broken)

    assert (
        "scout_bringup.cmd_vel_input_topic must match safety_mux.output_topic"
        in errors
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("can_interface", "can1", "must be 'can_scout'"),
        ("can_bitrate", 250_000, "must be 500000"),
    ],
)
def test_validate_config_rejects_unstable_can_contract(
    valid_config: dict,
    key: str,
    value: object,
    message: str,
) -> None:
    """The production config cannot fall back to incidental CAN numbering."""
    broken = deepcopy(valid_config)
    broken["scout_bringup"][key] = value

    errors = validate_config(broken)

    assert any(message in error for error in errors)


def test_validate_config_rejects_teleop_without_hold_to_run(valid_config: dict) -> None:
    """A one-shot enable configuration must never authorize manual motion."""
    broken = deepcopy(valid_config)
    broken["teleop_twist_joy"]["require_enable_button"] = False

    errors = validate_config(broken)

    assert "teleop_twist_joy.require_enable_button must be true" in errors


def test_validate_config_rejects_mismatched_teleop_permit_button(
    valid_config: dict,
) -> None:
    """The Twist frontend and safety authority must observe the same permit."""
    broken = deepcopy(valid_config)
    broken["safety_mux"]["teleop_enable_button"] += 1

    errors = validate_config(broken)

    assert (
        "safety_mux.teleop_enable_button must match "
        "teleop_twist_joy.enable_button"
    ) in errors


def test_validate_config_rejects_raised_motion_baseline(valid_config: dict) -> None:
    """Neither CAN recovery nor the final linear limit may be relaxed."""
    broken = deepcopy(valid_config)
    broken["scout_bringup"]["can_restart_ms"] = 0
    broken["safety_mux"]["max_linear_mps"] = 1.0
    broken["teleop_twist_joy"]["scale_linear.x"] = 1.0
    broken["teleop_twist_joy"]["scale_linear_turbo.x"] = 1.0

    errors = validate_config(broken)

    assert "scout_bringup.can_restart_ms must be 100" in errors
    assert "safety_mux.max_linear_mps must be 0.2" in errors


def test_validate_config_rejects_invalid_collision_zone(valid_config: dict) -> None:
    """The collision stop box must have increasing forward bounds."""
    broken = deepcopy(valid_config)
    broken["collision_monitor"]["min_x_m"] = 1.0
    broken["collision_monitor"]["max_x_m"] = 0.5
    errors = validate_config(broken)
    assert "collision_monitor.min_x_m must be less than max_x_m" in errors


def test_validate_config_rejects_route_topic_bypass(valid_config: dict) -> None:
    """Nav2 output and route supervision must observe the safety-owned topics."""
    broken = deepcopy(valid_config)
    broken["route_orchestrator"]["auto_command_topic"] = "/cmd_vel"
    broken["route_orchestrator"]["safety_diagnostics_topic"] = "/route/diagnostics"

    errors = validate_config(broken)

    assert (
        "route_orchestrator.auto_command_topic must match safety_mux.auto_topic"
        in errors
    )
    assert (
        "route_orchestrator.safety_diagnostics_topic must match "
        "safety_mux.diagnostics_topic"
    ) in errors


def test_validate_config_rejects_relative_fixed_route_path(valid_config: dict) -> None:
    """A route must not depend on the process working directory."""
    broken = deepcopy(valid_config)
    broken["route_orchestrator"]["fixed_route_path"] = "routes/demo.yaml"

    errors = validate_config(broken)

    assert "route_orchestrator.fixed_route_path must be absolute" in errors


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("min_x_m", float("nan")),
        ("max_x_m", float("inf")),
        ("min_z_m", float("-inf")),
        ("max_z_m", float("nan")),
    ],
)
def test_validate_config_rejects_non_finite_collision_zone_numbers(
    valid_config: dict,
    key: str,
    value: float,
) -> None:
    """Non-finite values cannot define a fail-closed stop zone."""
    broken = deepcopy(valid_config)
    broken["collision_monitor"][key] = value

    errors = validate_config(broken)

    assert any(f"collision_monitor.{key}" in error for error in errors)


def test_validate_config_file_reports_yaml_errors(tmp_path: Path) -> None:
    """Broken YAML files should produce a load error."""
    broken = tmp_path / "broken.yaml"
    broken.write_text(":\n- bad", encoding="utf-8")
    errors = validate_config_file(broken)
    assert len(errors) == 1
    assert "failed to load" in errors[0]


def test_load_config_roundtrip(valid_config: dict, tmp_path: Path) -> None:
    """Written YAML should reload to an equivalent mapping."""
    target = tmp_path / "system.yaml"
    payload = {
        section: {"ros__parameters": values}
        for section, values in valid_config.items()
    }
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_config(target)
    assert loaded["scout_bringup"]["host_ip"] == valid_config["scout_bringup"]["host_ip"]
