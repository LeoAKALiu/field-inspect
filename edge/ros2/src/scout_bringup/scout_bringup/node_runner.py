"""Deterministic signal handling for project-owned ROS 2 Python nodes."""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from typing import Optional

import rclpy
from rclpy.executors import Executor, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions


def run_node(
    factory: Callable[[], Node],
    *,
    args: Optional[list[str]] = None,
    executor_factory: Callable[[], Executor] = SingleThreadedExecutor,
) -> None:
    """Run one node without letting rclpy tear its context down in a signal handler.

    Humble's default signal handler can invalidate the context while an executor is
    deserializing a subscription.  The resulting teardown-only ``RuntimeError`` was
    observed on the Jetson health monitor.  Python signal handlers run in the main
    interpreter thread, so they only set an event here; cleanup and context shutdown
    remain ordered normal-context operations.
    """

    stop_requested = threading.Event()
    previous_handlers: dict[signal.Signals, signal.Handlers] = {}

    def request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)

    node: Node | None = None
    executor: Executor | None = None
    try:
        rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
        node = factory()
        executor = executor_factory()
        executor.add_node(node)
        while rclpy.ok() and not stop_requested.is_set():
            executor.spin_once(timeout_sec=0.1)
    finally:
        if executor is not None:
            if node is not None:
                executor.remove_node(node)
            executor.shutdown()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
