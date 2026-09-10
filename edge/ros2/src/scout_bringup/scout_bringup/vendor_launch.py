"""Vendor launch helpers for scout_bringup."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from launch.actions import (
    GroupAction,
    IncludeLaunchDescription,
    LogInfo,
    SetEnvironmentVariable,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node, SetRemap

try:  # LogError exists only in launch >= 3.x; Humble ships 1.0.x
    from launch.actions import LogError
except ImportError:
    LogError = LogInfo

from scout_bringup.livox_config import render_mid360s_config

SCOUT_BASE_COMMAND_TOPIC = "/cmd_vel"
MVS_LIBRARY_PATHS = frozenset({"/opt/MVS/lib/aarch64", "/opt/MVS/lib64"})


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _package_share(package_name: str) -> Path | None:
    try:
        return Path(get_package_share_directory(package_name))
    except PackageNotFoundError:
        return None


def without_mvs_library_paths(value: str | None = None) -> str:
    """
    Remove MVS SDK directories that can shadow the system libusb.

    Hikrobot's node carries its own RUNPATH to the SDK.  FAST-LIO, however,
    loads PCL and must resolve the system libusb containing libusb_set_option.
    Keep the cleanup scoped to the FAST-LIO launch group instead of changing
    the operator's shell environment.
    """
    library_path = os.environ.get("LD_LIBRARY_PATH", "") if value is None else value
    entries = [
        entry
        for entry in library_path.split(":")
        if entry and entry.rstrip("/") not in MVS_LIBRARY_PATHS
    ]
    return ":".join(dict.fromkeys(entries))


def scout_base_actions(config: dict[str, Any], *, enabled: bool) -> list:
    """Return launch actions for the SCOUT Mini base driver."""
    if not enabled:
        return []

    safe_command_topic = config["cmd_vel_input_topic"]
    share = _package_share("scout_base")
    if share is None:
        return [
            LogError(
                msg=(
                    "VENDOR: scout_base is not installed; import/build "
                    "third_party/scout_ros2 before start_scout:=true"
                )
            )
        ]

    launch_file = share / "launch" / "scout_mini_base.launch.py"
    if launch_file.is_file():
        return [
            GroupAction(
                actions=[
                    SetRemap(
                        # scout_base compiles this subscription name into its API.
                        src=SCOUT_BASE_COMMAND_TOPIC,
                        dst=safe_command_topic,
                    ),
                    IncludeLaunchDescription(
                        PythonLaunchDescriptionSource(str(launch_file)),
                        launch_arguments={
                            "port_name": config["can_interface"],
                            "base_frame": config["base_frame"],
                            "odom_frame": "odom",
                            "odom_topic_name": "odom",
                        }.items(),
                    ),
                ]
            )
        ]

    return [
        Node(
            package="scout_base",
            executable="scout_base_node",
            name="scout_base",
            output="screen",
            remappings=[(SCOUT_BASE_COMMAND_TOPIC, safe_command_topic)],
            parameters=[
                {
                    "port_name": config["can_interface"],
                    "base_frame": config["base_frame"],
                    "odom_frame": "odom",
                    "odom_topic_name": "odom",
                    "is_scout_mini": True,
                    "is_omni_wheel": False,
                    "simulated_robot": False,
                }
            ],
        )
    ]


def teleop_actions(config: dict[str, Any], *, enabled: bool) -> list:
    """Return the joystick hold-to-run path feeding only the safety mux."""
    if not enabled:
        return []

    safety = config["safety_mux"]
    joy_topic = safety["joy_topic"]
    return [
        Node(
            package="joy",
            executable="joy_node",
            name="joy_node",
            output="screen",
            parameters=[config["joy_node"]],
            remappings=[("joy", joy_topic)],
        ),
        Node(
            package="teleop_twist_joy",
            executable="teleop_node",
            name="teleop_twist_joy",
            output="screen",
            parameters=[config["teleop_twist_joy"]],
            remappings=[
                ("joy", joy_topic),
                ("cmd_vel", safety["teleop_topic"]),
            ],
        ),
    ]


def livox_actions(config: dict[str, Any], *, enabled: bool, runtime_dir: Path) -> list:
    """Return launch actions for Livox Mid-360S custom-message publishing."""
    if not enabled:
        return []

    if _package_share("livox_ros_driver2") is None:
        return [
            LogError(
                msg=(
                    "VENDOR: livox_ros_driver2 is not installed; run "
                    "./scripts/install_deps.sh before start_livox:=true"
                )
            )
        ]

    config_path = render_mid360s_config(
        config["host_ip"],
        config["lidar_ip"],
        runtime_dir / "MID360s_config.json",
    )
    return [
        LogInfo(msg=f"VENDOR: using Livox config {config_path}"),
        Node(
            package="livox_ros_driver2",
            executable="livox_ros_driver2_node",
            name="livox_lidar_publisher",
            output="screen",
            parameters=[
                {
                    "xfer_format": int(config.get("livox_xfer_format", 1)),
                    "multi_topic": 0,
                    "data_src": 0,
                    "publish_freq": float(config.get("livox_publish_freq_hz", 10.0)),
                    "output_data_type": 0,
                    "frame_id": config["lidar_frame"],
                    "user_config_path": str(config_path),
                    "cmdline_input_bd_code": "livox0000000001",
                }
            ],
        ),
    ]


def fast_lio_actions(config: dict[str, Any], *, enabled: bool) -> list:
    """Return launch actions for FAST-LIO mapping."""
    if not enabled:
        return []

    share = _package_share("fast_lio")
    if share is None:
        return [
            LogError(
                msg=(
                    "VENDOR: fast_lio is not installed; import/build "
                    "third_party/FAST_LIO before start_fast_lio:=true"
                )
            )
        ]

    launch_file = share / "launch" / "mapping.launch.py"
    if not launch_file.is_file():
        return [LogError(msg="VENDOR: fast_lio mapping.launch.py not found in package share")]

    return [
        GroupAction(
            actions=[
                SetEnvironmentVariable(
                    name="LD_LIBRARY_PATH",
                    value=without_mvs_library_paths(),
                ),
                SetRemap(src="/tf", dst="/fast_lio/tf_raw"),
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(str(launch_file)),
                    launch_arguments={
                        "config_file": str(config.get("fast_lio_config_file", "mid360.yaml")),
                        "rviz": "true"
                        if str(config.get("fast_lio_rviz", "false")).lower()
                        in {"1", "true", "yes", "on"}
                        else "false",
                    }.items(),
                ),
            ]
        )
    ]
