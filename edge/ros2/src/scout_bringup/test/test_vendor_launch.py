"""Tests for vendor launch safety wiring."""

from __future__ import annotations

from launch.actions import GroupAction, SetEnvironmentVariable
from launch.launch_context import LaunchContext
from launch.utilities import perform_substitutions
from launch_ros.actions import Node, SetRemap

from scout_bringup import vendor_launch


def test_scout_launch_remaps_raw_velocity_to_safe_output(monkeypatch, tmp_path) -> None:
    """The upstream launch path must never leave the base on raw /cmd_vel."""
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    (launch_dir / "scout_mini_base.launch.py").touch()
    monkeypatch.setattr(vendor_launch, "_package_share", lambda _name: tmp_path)

    actions = vendor_launch.scout_base_actions(
        {
            "can_interface": "can0",
            "base_frame": "base_link",
            "cmd_vel_input_topic": "/cmd_vel_safe",
        },
        enabled=True,
    )

    assert len(actions) == 1
    assert isinstance(actions[0], GroupAction)
    remaps = [
        action
        for action in actions[0].get_sub_entities()
        if isinstance(action, SetRemap)
    ]
    assert len(remaps) == 1
    context = LaunchContext()
    assert (
        perform_substitutions(context, remaps[0].src)
        == vendor_launch.SCOUT_BASE_COMMAND_TOPIC
        == "/cmd_vel"
    )
    assert perform_substitutions(context, remaps[0].dst) == "/cmd_vel_safe"


def test_teleop_launch_feeds_only_the_teleop_safety_input() -> None:
    """Joystick commands must enter the mux instead of bypassing /cmd_vel_safe."""
    config = {
        "joy_node": {"device_id": 0},
        "teleop_twist_joy": {
            "require_enable_button": True,
            "enable_button": 5,
        },
        "safety_mux": {
            "joy_topic": "/joy",
            "teleop_topic": "/cmd_vel_teleop",
            "output_topic": "/cmd_vel_safe",
        },
    }

    actions = vendor_launch.teleop_actions(config, enabled=True)

    assert len(actions) == 2
    assert all(isinstance(action, Node) for action in actions)
    joy, teleop = actions
    assert (joy.node_package, joy.node_executable) == ("joy", "joy_node")
    assert (teleop.node_package, teleop.node_executable) == (
        "teleop_twist_joy",
        "teleop_node",
    )
    context = LaunchContext()
    remappings = {
        perform_substitutions(context, source): perform_substitutions(context, target)
        for source, target in teleop._Node__remappings
    }
    assert remappings == {"joy": "/joy", "cmd_vel": "/cmd_vel_teleop"}
    assert "/cmd_vel_safe" not in remappings.values()


def test_teleop_launch_is_empty_when_disabled() -> None:
    """The joystick stack remains opt-in for unattended launches."""
    assert vendor_launch.teleop_actions({}, enabled=False) == []


def test_fast_lio_launch_quarantines_vendor_tf(monkeypatch, tmp_path) -> None:
    """FAST-LIO's camera_init->body TF must not enter the authoritative tree."""
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    (launch_dir / "mapping.launch.py").touch()
    monkeypatch.setattr(vendor_launch, "_package_share", lambda _name: tmp_path)

    actions = vendor_launch.fast_lio_actions({}, enabled=True)

    assert len(actions) == 1
    assert isinstance(actions[0], GroupAction)
    remap = next(
        action
        for action in actions[0].get_sub_entities()
        if isinstance(action, SetRemap)
    )
    context = LaunchContext()
    assert perform_substitutions(context, remap.src) == "/tf"
    assert perform_substitutions(context, remap.dst) == "/fast_lio/tf_raw"


def test_mvs_paths_are_removed_from_fast_lio_environment(monkeypatch, tmp_path) -> None:
    """The MVS libusb must not shadow the system library used by PCL."""
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    (launch_dir / "mapping.launch.py").touch()
    monkeypatch.setattr(vendor_launch, "_package_share", lambda _name: tmp_path)
    monkeypatch.setenv(
        "LD_LIBRARY_PATH",
        "/opt/MVS/lib/aarch64:/opt/ros/humble/lib:/opt/MVS/lib64:"
        "/opt/ros/humble/lib:/usr/local/lib",
    )

    group = vendor_launch.fast_lio_actions({}, enabled=True)[0]
    environment = next(
        action
        for action in group.get_sub_entities()
        if isinstance(action, SetEnvironmentVariable)
    )
    context = LaunchContext()

    assert perform_substitutions(context, environment.name) == "LD_LIBRARY_PATH"
    assert perform_substitutions(context, environment.value) == (
        "/opt/ros/humble/lib:/usr/local/lib"
    )


def test_mvs_path_cleanup_preserves_unrelated_order() -> None:
    """The scoped fix must not rewrite unrelated library search paths."""
    assert vendor_launch.without_mvs_library_paths(
        "/vendor/a:/opt/MVS/lib/aarch64/:/vendor/b"
    ) == "/vendor/a:/vendor/b"
