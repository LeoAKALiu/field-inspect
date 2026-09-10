"""Launch smoke tests for demo.launch.py."""

from __future__ import annotations

import unittest

import launch_testing.actions
import launch_testing.asserts
import launch_testing.markers
import pytest
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


@pytest.mark.launch_test
def generate_test_description() -> LaunchDescription:
    """Generate the launch test description."""
    launch_file = PathJoinSubstitution(
        [FindPackageShare("scout_bringup"), "launch", "demo.launch.py"]
    )
    return LaunchDescription(
        [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(launch_file),
                launch_arguments={
                    "start_camera": "false",
                    "start_safety": "true",
                    "start_inspection": "false",
                }.items(),
            ),
            launch_testing.actions.ReadyToTest(),
        ]
    )


class TestDemoLaunch(unittest.TestCase):
    """Basic launch lifecycle checks."""

    def test_launch_starts(self, proc_info) -> None:
        """The demo stack should remain alive after startup."""
        proc_info.assertWaitForStartup(process="safety_mux", timeout=10)

    def test_node_ready(self, proc_output) -> None:
        """safety_mux reaches its main loop before teardown begins."""
        proc_output.assertWaitFor("Arbitration state", process="safety_mux", timeout=10)


@launch_testing.post_shutdown_test()
class TestDemoLaunchAfterShutdown(unittest.TestCase):
    """Post-teardown checks."""

    def test_launch_shuts_down(self, proc_info) -> None:
        """Launch testing should terminate cleanly."""
        launch_testing.asserts.assertExitCodes(proc_info, process="safety_mux")
