"""Tests for preflight diagnostics."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scout_bringup.config_validator import load_config
from scout_bringup.preflight import (
    CanInterfaceStatus,
    Diagnostic,
    collect_launch_diagnostics,
    collect_preflight_diagnostics,
)


@patch("scout_bringup.preflight._ros_package_exists", return_value=False)
def test_collect_preflight_warns_on_missing_vendor_source(_mock_package, tmp_path: Path) -> None:
    """Missing vendor sources should produce actionable warnings."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=False,
        start_scout=True,
        start_livox=False,
        start_fast_lio=False,
        workspace_root=tmp_path,
    )

    codes = {item.code for item in diagnostics}
    assert "VENDOR_SOURCE_MISSING" in codes


def test_collect_preflight_warns_on_unverified_extrinsics() -> None:
    """Placeholder extrinsics must be surfaced explicitly."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)
    config["scout_bringup"]["extrinsics_verified"] = False

    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=False,
        start_scout=False,
        start_livox=False,
        start_fast_lio=False,
        workspace_root=None,
    )

    assert any(item.code == "EXTRINSICS_UNVERIFIED" for item in diagnostics)


@patch("scout_bringup.preflight._interface_has_ip", return_value=False)
@patch("scout_bringup.preflight._interface_exists", return_value=True)
def test_collect_preflight_warns_when_sensor_ip_is_missing(_mock_exists, _mock_has_ip) -> None:
    """A linked Ethernet interface without the configured host IP is not ready."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=False,
        start_scout=False,
        start_livox=True,
        start_fast_lio=False,
        workspace_root=None,
    )

    assert any(item.code == "NET_HOST_IP_MISSING" for item in diagnostics)


@patch("scout_bringup.preflight._ping_host", return_value=False)
def test_collect_preflight_warns_when_camera_unreachable(_mock_ping) -> None:
    """Unreachable cameras should not fail silently when enabled."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=True,
        start_scout=False,
        start_livox=False,
        start_fast_lio=False,
        workspace_root=None,
    )

    assert any(item.code == "CAMERA_UNREACHABLE" for item in diagnostics)


def test_diagnostic_format() -> None:
    """Diagnostics should render as stable single-line messages."""
    item = Diagnostic("warn", "TEST", "message")
    assert item.format() == "WARN  [TEST] message"


@patch("scout_bringup.preflight._ros_package_exists", return_value=True)
@patch("scout_bringup.preflight._running_kernel_release", return_value="5.15.test")
@patch("scout_bringup.preflight._gs_usb_module_release", return_value="5.15.test")
@patch(
    "scout_bringup.preflight._inspect_can_interface",
    return_value=CanInterfaceStatus(True, True, True, 500_000, 100),
)
def test_collect_preflight_accepts_ready_stable_can(
    _mock_can,
    _mock_module,
    _mock_kernel,
    _mock_package,
) -> None:
    """A matching driver and ready 500 kbit/s link should pass the CAN gate."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=False,
        start_scout=True,
        start_livox=False,
        start_fast_lio=False,
        workspace_root=None,
    )

    assert not any(item.code.startswith("CAN_") for item in diagnostics)


@patch("scout_bringup.preflight._ros_package_exists", return_value=True)
@patch("scout_bringup.preflight._running_kernel_release", return_value="5.15.current")
@patch("scout_bringup.preflight._gs_usb_module_release", return_value="5.15.old")
def test_collect_preflight_blocks_mismatched_gs_usb_module(
    _mock_module,
    _mock_kernel,
    _mock_package,
) -> None:
    """A module built for another kernel cannot be treated as ready."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=False,
        start_scout=True,
        start_livox=False,
        start_fast_lio=False,
        workspace_root=None,
    )

    mismatch = next(item for item in diagnostics if item.code == "CAN_MODULE_MISMATCH")
    assert mismatch.level == "error"


@patch("scout_bringup.preflight._ros_package_exists", return_value=True)
@patch("scout_bringup.preflight._running_kernel_release", return_value="5.15.current")
@patch("scout_bringup.preflight._gs_usb_module_release", return_value="5.15.old")
@patch(
    "scout_bringup.preflight._inspect_can_interface",
    return_value=CanInterfaceStatus(True, True, True, 500_000, 100),
)
def test_launch_diagnostics_promote_can_gate_failure_to_startup_error(
    _mock_can,
    _mock_module,
    _mock_kernel,
    _mock_package,
) -> None:
    """The launch path must stop instead of merely logging a CAN error."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"

    errors, diagnostics = collect_launch_diagnostics(
        config_path,
        (("start_scout", "true"),),
        workspace_root=None,
    )

    assert any("CAN_MODULE_MISMATCH" in error for error in errors)
    assert any(item.code == "CAN_MODULE_MISMATCH" for item in diagnostics)


@pytest.mark.parametrize(
    ("state", "expected_code"),
    [
        (CanInterfaceStatus(False, False, False, None, None), "CAN_IFACE_MISSING"),
        (CanInterfaceStatus(True, False, True, None, None), "CAN_IFACE_NOT_SOCKETCAN"),
        (CanInterfaceStatus(True, True, False, 500_000, 100), "CAN_IFACE_DOWN"),
        (CanInterfaceStatus(True, True, True, 250_000, 100), "CAN_BITRATE_MISMATCH"),
        (CanInterfaceStatus(True, True, True, 500_000, 0), "CAN_RESTART_MISMATCH"),
    ],
)
@patch("scout_bringup.preflight._ros_package_exists", return_value=True)
@patch("scout_bringup.preflight._running_kernel_release", return_value="5.15.test")
@patch("scout_bringup.preflight._gs_usb_module_release", return_value="5.15.test")
def test_collect_preflight_blocks_unready_can_interface(
    _mock_module,
    _mock_kernel,
    _mock_package,
    state: CanInterfaceStatus,
    expected_code: str,
) -> None:
    """Every invalid SocketCAN state must fail closed with a stable reason."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    with patch("scout_bringup.preflight._inspect_can_interface", return_value=state):
        diagnostics = collect_preflight_diagnostics(
            config,
            start_camera=False,
            start_scout=True,
            start_livox=False,
            start_fast_lio=False,
            workspace_root=None,
        )

    diagnostic = next(item for item in diagnostics if item.code == expected_code)
    assert diagnostic.level == "error"


@patch(
    "scout_bringup.preflight._ros_package_exists",
    side_effect=lambda package: package != "joy",
)
def test_collect_preflight_blocks_missing_teleop_package(_mock_package) -> None:
    """An enabled hold-to-run path cannot start with half its packages absent."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=False,
        start_scout=False,
        start_livox=False,
        start_fast_lio=False,
        start_teleop=True,
        workspace_root=None,
    )

    missing = next(item for item in diagnostics if item.code == "TELEOP_PACKAGE_MISSING")
    assert missing.level == "error"
    assert "joy" in missing.message


@patch(
    "scout_bringup.preflight._ros_package_exists",
    side_effect=lambda package: package != "rosbag2_storage_mcap",
)
def test_collect_preflight_blocks_missing_mcap_storage(_mock_package) -> None:
    """A run manager cannot start when its configured storage plugin is absent."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=False,
        start_scout=False,
        start_livox=False,
        start_fast_lio=False,
        start_inspection=True,
        workspace_root=None,
    )

    missing = next(item for item in diagnostics if item.code == "MCAP_STORAGE_MISSING")
    assert missing.level == "error"


@patch(
    "scout_bringup.preflight._ros_package_exists",
    side_effect=lambda package: package != "rosbag2_storage_mcap",
)
def test_launch_diagnostics_promote_missing_mcap_to_startup_error(
    _mock_package,
) -> None:
    """The default launch must stop before presenting a broken recorder."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"

    errors, diagnostics = collect_launch_diagnostics(
        config_path,
        (
            ("start_camera", "false"),
            ("start_inspection", "true"),
        ),
        workspace_root=None,
    )

    assert any("MCAP_STORAGE_MISSING" in error for error in errors)
    assert any(item.code == "MCAP_STORAGE_MISSING" for item in diagnostics)


def test_launch_diagnostics_reject_teleop_without_safety_boundary() -> None:
    """Teleop must never be launched when /cmd_vel_safe is not being enforced."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"

    errors, _diagnostics = collect_launch_diagnostics(
        config_path,
        (
            ("start_safety", "false"),
            ("start_teleop", "true"),
        ),
        workspace_root=None,
    )

    assert "start_teleop=true requires start_safety=true" in errors


@patch("scout_bringup.config_validator.load_config")
def test_route_launch_requires_the_complete_supervised_stack(_mock_load) -> None:
    """A fixed route cannot bypass sensing, recording, safety or operator controls."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)
    config["scout_bringup"]["extrinsics_verified"] = False
    _mock_load.return_value = config

    errors, _diagnostics = collect_launch_diagnostics(
        config_path,
        (
            ("start_camera", "false"),
            ("start_inspection", "false"),
            ("start_route", "true"),
        ),
        workspace_root=None,
    )

    for requirement in (
        "start_scout",
        "start_livox",
        "start_fast_lio",
        "start_collision",
        "start_teleop",
        "start_inspection",
        "start_camera",
    ):
        assert f"start_route=true requires {requirement}=true" in errors
    assert "start_route=true requires scout_bringup.extrinsics_verified=true" in errors
    assert "start_route=true requires inspection_manager.run_mode=production" in errors


@patch("scout_bringup.preflight._ros_package_exists", return_value=True)
def test_route_preflight_rejects_unverified_controller_and_missing_route(
    _mock_package,
) -> None:
    """Installing Nav2 alone never authorizes motion with seed tuning."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "system.yaml"
    config = load_config(config_path)

    diagnostics = collect_preflight_diagnostics(
        config,
        start_camera=False,
        start_scout=False,
        start_livox=False,
        start_fast_lio=False,
        start_route=True,
        workspace_root=None,
    )

    codes = {item.code for item in diagnostics}
    assert "ROUTE_CONTROLLER_UNVERIFIED" in codes
    assert "FIXED_ROUTE_MISSING" in codes
