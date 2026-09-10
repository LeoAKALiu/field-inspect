"""Regression tests for TF health lookup result handling."""

from __future__ import annotations

from scout_bringup.health_monitor import HealthMonitor


class FakeBuffer:
    """Return a configured tf2-style result or exception."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def can_transform(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.result


def monitor_with_buffer(buffer: FakeBuffer) -> HealthMonitor:
    """Build only the state needed by the pure lookup wrapper."""
    monitor = HealthMonitor.__new__(HealthMonitor)
    monitor._tf_target = "base_link"
    monitor._tf_source = "odom"
    monitor._tf_timeout_ms = 1
    monitor._tf_buffer = buffer
    return monitor


def test_tf_lookup_normalizes_non_boolean_results() -> None:
    """A None result from Humble tf2 must publish false instead of crashing Bool."""
    assert monitor_with_buffer(FakeBuffer(None))._lookup_tf_ok() is False
    assert monitor_with_buffer(FakeBuffer(object()))._lookup_tf_ok() is True


def test_tf_lookup_fails_closed_on_exception() -> None:
    """Transient tf2 errors must remain a false health heartbeat."""
    monitor = monitor_with_buffer(FakeBuffer(error=RuntimeError("tf failed")))
    assert monitor._lookup_tf_ok() is False
