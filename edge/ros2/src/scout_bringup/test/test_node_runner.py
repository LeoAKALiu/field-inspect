"""Tests for ordered project-owned ROS node shutdown."""

from __future__ import annotations

import signal
import subprocess
import sys
import time


def test_run_node_handles_sigint_without_context_race() -> None:
    code = (
        "from rclpy.node import Node\n"
        "from scout_bringup.node_runner import run_node\n"
        "run_node(lambda: Node('node_runner_shutdown_test'))\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(0.5)
    process.send_signal(signal.SIGINT)
    output, _ = process.communicate(timeout=10)

    assert process.returncode == 0, output
    assert "RuntimeError" not in output
    assert "RCLError" not in output
