"""Unit tests for health-monitor state that does not require ROS spinning."""

import socket
import struct

from scout_bringup.health_monitor import ScoutCanRxMonitor


class FakeCanSocket:
    """Minimal nonblocking SocketCAN double for deterministic frame tests."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.bound_to = None
        self.filter_bytes = b""
        self.error_filter_bytes = b""
        self.closed = False

    def setsockopt(self, level: int, option: int, value: bytes) -> None:
        assert level == socket.SOL_CAN_RAW
        if option == socket.CAN_RAW_FILTER:
            self.filter_bytes = value
        elif option == socket.CAN_RAW_ERR_FILTER:
            self.error_filter_bytes = value
        else:
            raise AssertionError(f"unexpected SocketCAN option {option}")

    def setblocking(self, enabled: bool) -> None:
        assert enabled is False

    def bind(self, address: tuple[str]) -> None:
        self.bound_to = address

    def recv(self, _size: int) -> bytes:
        if not self.frames:
            raise BlockingIOError
        return self.frames.pop(0)

    def close(self) -> None:
        self.closed = True


class BindFailCanSocket(FakeCanSocket):
    """Socket double for an absent/down interface."""

    def bind(self, _address: tuple[str]) -> None:
        raise OSError("interface missing")


def _can_frame(can_id: int, dlc: int = 8) -> bytes:
    return struct.pack("=IB3x8s", can_id, dlc, bytes(8))


def test_scout_can_monitor_accepts_only_real_scout_state_frames() -> None:
    """TX traffic and CAN error frames cannot make the chassis look healthy."""
    fake = FakeCanSocket()
    monitor = ScoutCanRxMonitor(
        "can_scout",
        timeout_ms=500,
        socket_factory=lambda *_args: fake,
    )

    fake.frames.extend(
        [
            _can_frame(0x130),
            _can_frame(0x151),
            _can_frame(socket.CAN_ERR_FLAG | 0x211),
        ]
    )
    assert not monitor.poll(now_ns=1_000_000_000)

    fake.frames.append(_can_frame(0x211))
    assert monitor.poll(now_ns=1_100_000_000)
    assert monitor.poll(now_ns=1_499_000_000)
    assert not monitor.poll(now_ns=1_501_000_000)


def test_scout_can_monitor_installs_exact_state_filters() -> None:
    """Kernel filtering must subscribe only to the manual's V2 state ID."""
    fake = FakeCanSocket()
    monitor = ScoutCanRxMonitor(
        "can_scout",
        timeout_ms=500,
        socket_factory=lambda *_args: fake,
    )

    assert not monitor.poll(now_ns=1_000_000_000)
    filters = {
        pair
        for pair in struct.iter_unpack("=II", fake.filter_bytes)
    }
    exact_standard_data_mask = (
        socket.CAN_SFF_MASK | socket.CAN_EFF_FLAG | socket.CAN_RTR_FLAG
    )
    assert filters == {
        (0x211, exact_standard_data_mask),
    }
    assert struct.unpack("=I", fake.error_filter_bytes) == (0,)
    assert fake.bound_to == ("can_scout",)


def test_scout_can_monitor_does_not_retimestamp_backlogged_frames() -> None:
    """A delayed timer must not make an old queued frame look newly received."""
    fake = FakeCanSocket()
    monitor = ScoutCanRxMonitor(
        "can_scout",
        timeout_ms=500,
        socket_factory=lambda *_args: fake,
    )

    assert not monitor.poll(now_ns=1_000_000_000)
    fake.frames.append(_can_frame(0x211))
    assert not monitor.poll(now_ns=2_000_000_000)

    fake.frames.append(_can_frame(0x211))
    assert monitor.poll(now_ns=2_100_000_000)


def test_scout_can_monitor_closes_socket_when_bind_fails() -> None:
    """Polling a missing interface repeatedly must not leak raw sockets."""
    opened: list[BindFailCanSocket] = []

    def socket_factory(*_args) -> BindFailCanSocket:
        raw_socket = BindFailCanSocket()
        opened.append(raw_socket)
        return raw_socket

    monitor = ScoutCanRxMonitor(
        "can_scout",
        timeout_ms=500,
        socket_factory=socket_factory,
    )

    assert not monitor.poll(now_ns=1_000_000_000)
    assert not monitor.poll(now_ns=1_100_000_000)
    assert len(opened) == 2
    assert all(raw_socket.closed for raw_socket in opened)
