from __future__ import annotations

import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, OpaqueFunction, Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

try:  # LogError exists only in launch >= 3.x; Humble ships 1.0.x
    from launch.actions import LogError
except ImportError:
    LogError = LogInfo

from scout_bringup.config_validator import load_config
from scout_bringup.preflight import collect_launch_diagnostics
from scout_bringup.vendor_launch import (
    fast_lio_actions,
    livox_actions,
    scout_base_actions,
    teleop_actions,
)


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _workspace_root() -> Path | None:
    share = Path(get_package_share_directory("scout_bringup"))
    candidate = share.parent.parent.parent
    if (candidate / "src").is_dir():
        return candidate
    return None


def _runtime_dir() -> Path:
    root = os.environ.get("SCOUT_MINI_RUNTIME_DIR", "/tmp/scout_mini_runtime")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _static_transform_nodes(config: dict, *, start_fast_lio: bool) -> list[Node]:
    bringup = config["scout_bringup"]
    base_frame = bringup["base_frame"]
    nodes: list[Node] = []

    for child_key, xyz_key, rpy_key in (
        ("body_frame", "base_to_body_xyz_m", "base_to_body_rpy_rad"),
        ("lidar_frame", "base_to_lidar_xyz_m", "base_to_lidar_rpy_rad"),
        ("camera_frame", "base_to_camera_xyz_m", "base_to_camera_rpy_rad"),
    ):
        xyz = bringup[xyz_key]
        rpy = bringup[rpy_key]
        child_frame = bringup[child_key]
        nodes.append(
            Node(
                package="tf2_ros",
                executable="static_transform_publisher",
                name=f"{child_frame}_static_tf",
                arguments=[
                    "--x",
                    str(xyz[0]),
                    "--y",
                    str(xyz[1]),
                    "--z",
                    str(xyz[2]),
                    "--roll",
                    str(rpy[0]),
                    "--pitch",
                    str(rpy[1]),
                    "--yaw",
                    str(rpy[2]),
                    "--frame-id",
                    base_frame,
                    "--child-frame-id",
                    child_frame,
                ],
                output="screen",
            )
        )
    camera_frame = bringup["camera_frame"]
    camera_optical_frame = bringup["camera_optical_frame"]
    nodes.append(
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name=f"{camera_optical_frame}_static_tf",
            arguments=[
                "--x",
                "0",
                "--y",
                "0",
                "--z",
                "0",
                "--roll",
                str(-1.5707963267948966),
                "--pitch",
                "0",
                "--yaw",
                str(-1.5707963267948966),
                "--frame-id",
                camera_frame,
                "--child-frame-id",
                camera_optical_frame,
            ],
            output="screen",
        )
    )
    if start_fast_lio:
        localization = config["localization_adapter"]
        map_frame = localization["map_frame"]
        input_global_frame = localization["input_global_frame"]
        if map_frame != input_global_frame:
            nodes.append(
                Node(
                    package="tf2_ros",
                    executable="static_transform_publisher",
                    name="fast_lio_global_frame_alias",
                    arguments=[
                        "--x",
                        "0",
                        "--y",
                        "0",
                        "--z",
                        "0",
                        "--roll",
                        "0",
                        "--pitch",
                        "0",
                        "--yaw",
                        "0",
                        "--frame-id",
                        map_frame,
                        "--child-frame-id",
                        input_global_frame,
                    ],
                    output="screen",
                )
            )
    return nodes


def _launch_setup(context, *args, **kwargs):
    config_path = Path(LaunchConfiguration("config").perform(context))
    start_camera = _parse_bool(LaunchConfiguration("start_camera").perform(context))
    start_safety = _parse_bool(LaunchConfiguration("start_safety").perform(context))
    start_inspection = _parse_bool(LaunchConfiguration("start_inspection").perform(context))
    start_scout = _parse_bool(LaunchConfiguration("start_scout").perform(context))
    start_livox = _parse_bool(LaunchConfiguration("start_livox").perform(context))
    start_fast_lio = _parse_bool(LaunchConfiguration("start_fast_lio").perform(context))
    start_collision = _parse_bool(LaunchConfiguration("start_collision").perform(context))
    start_teleop = _parse_bool(LaunchConfiguration("start_teleop").perform(context))
    start_route = _parse_bool(LaunchConfiguration("start_route").perform(context))

    launch_arguments = (
        ("start_camera", str(start_camera).lower()),
        ("start_scout", str(start_scout).lower()),
        ("start_livox", str(start_livox).lower()),
        ("start_fast_lio", str(start_fast_lio).lower()),
        ("start_inspection", str(start_inspection).lower()),
        ("start_collision", str(start_collision).lower()),
        ("start_safety", str(start_safety).lower()),
        ("start_teleop", str(start_teleop).lower()),
        ("start_route", str(start_route).lower()),
    )
    errors, diagnostics = collect_launch_diagnostics(
        config_path,
        launch_arguments,
        workspace_root=_workspace_root(),
    )

    actions = []
    if errors:
        for error in errors:
            actions.append(LogError(msg=f"CONFIG: {error}"))
        actions.append(Shutdown(reason="invalid system.yaml"))
        return actions

    for diagnostic in diagnostics:
        log_action = LogError if diagnostic.level == "error" else LogInfo
        actions.append(log_action(msg=f"PREFLIGHT: {diagnostic.format()}"))

    config = load_config(config_path)
    bringup = config["scout_bringup"]
    actions.extend(_static_transform_nodes(config, start_fast_lio=start_fast_lio))

    runtime_dir = _runtime_dir()
    actions.extend(scout_base_actions(bringup, enabled=start_scout))
    actions.extend(livox_actions(bringup, enabled=start_livox, runtime_dir=runtime_dir))
    actions.extend(fast_lio_actions(bringup, enabled=start_fast_lio))

    if start_fast_lio:
        localization_params = {
            **config["localization_adapter"],
            "extrinsics_verified": bringup["extrinsics_verified"],
        }
        actions.append(
            Node(
                package="scout_bringup",
                executable="localization_adapter",
                name="localization_adapter",
                parameters=[localization_params],
                output="screen",
            )
        )

    if start_safety:
        health_params = config.get("health_monitor", {})
        health_params = {
            **health_params,
            "monitor_lidar": start_livox,
            "monitor_tf": start_scout or start_fast_lio,
            "monitor_chassis": start_scout,
        }
        if start_fast_lio:
            health_params["tf_healthy_topic"] = health_params[
                "odom_tf_healthy_topic"
            ]
        actions.append(
            Node(
                package="scout_bringup",
                executable="health_monitor",
                name="health_monitor",
                parameters=[health_params],
                output="screen",
            )
        )
        safety_params = {
            **config.get("safety_mux", {}),
            "require_obstacle_clear": start_collision,
        }
        actions.append(
            Node(
                package="safety_mux",
                executable="safety_mux_node",
                name="safety_mux",
                parameters=[safety_params],
                output="screen",
            )
        )

    if start_collision:
        actions.append(
            Node(
                package="safety_mux",
                executable="collision_monitor_node",
                name="collision_monitor",
                parameters=[config.get("collision_monitor", {})],
                output="screen",
            )
        )

    actions.extend(teleop_actions(config, enabled=start_teleop))

    if start_route:
        route_params = config["route_orchestrator"]
        share = Path(get_package_share_directory("scout_bringup"))
        controller_config = share / "config" / route_params["controller_config"]
        orchestrator_params = {
            key: value
            for key, value in route_params.items()
            if key != "controller_config"
        }
        actions.extend(
            [
                Node(
                    package="nav2_controller",
                    executable="controller_server",
                    name="controller_server",
                    parameters=[str(controller_config)],
                    remappings=[("cmd_vel", route_params["auto_command_topic"])],
                    output="screen",
                ),
                Node(
                    package="nav2_lifecycle_manager",
                    executable="lifecycle_manager",
                    name="lifecycle_manager_route",
                    parameters=[str(controller_config)],
                    output="screen",
                ),
                Node(
                    package="inspection_pipeline",
                    executable="inspection_route_orchestrator",
                    name="route_orchestrator",
                    parameters=[
                        {
                            **orchestrator_params,
                            "max_linear_mps": config["safety_mux"]["max_linear_mps"],
                        }
                    ],
                    output="screen",
                ),
            ]
        )

    if start_camera:
        actions.append(
            Node(
                package="hik_camera_ros2",
                executable="hik_camera_node",
                name="hik_camera_node",
                parameters=[str(config_path)],
                output="screen",
            )
        )

    if start_inspection:
        inspection_params = config.get("inspection_manager", {})
        inspection_params = {**inspection_params, "config_path": str(config_path)}
        actions.append(
            Node(
                package="inspection_pipeline",
                executable="inspection_manager",
                name="inspection_manager",
                parameters=[inspection_params],
                output="screen",
            )
        )

    return actions


def generate_launch_description():
    share = Path(get_package_share_directory("scout_bringup"))
    default_config = str(share / "config" / "system.yaml")

    return LaunchDescription(
        [
            DeclareLaunchArgument("config", default_value=default_config),
            DeclareLaunchArgument("start_camera", default_value="true"),
            DeclareLaunchArgument("start_safety", default_value="true"),
            DeclareLaunchArgument("start_inspection", default_value="true"),
            DeclareLaunchArgument("start_scout", default_value="false"),
            DeclareLaunchArgument("start_livox", default_value="false"),
            DeclareLaunchArgument("start_fast_lio", default_value="false"),
            DeclareLaunchArgument("start_collision", default_value="false"),
            DeclareLaunchArgument("start_teleop", default_value="false"),
            DeclareLaunchArgument("start_route", default_value="false"),
            OpaqueFunction(function=_launch_setup),
        ]
    )
