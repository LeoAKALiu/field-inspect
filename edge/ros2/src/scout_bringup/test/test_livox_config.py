"""Tests for Livox config rendering."""

from __future__ import annotations

import json
from pathlib import Path

from scout_bringup.livox_config import build_mid360s_config, render_mid360s_config


def test_build_mid360s_config_uses_host_and_lidar_ips() -> None:
    """Generated Livox config should mirror bringup network parameters."""
    payload = build_mid360s_config("192.168.1.50", "192.168.1.191")
    assert payload["Mid360s"]["host_net_info"][0]["host_ip"] == "192.168.1.50"
    assert payload["lidar_configs"][0]["ip"] == "192.168.1.191"


def test_render_mid360s_config_writes_json(tmp_path: Path) -> None:
    """Rendered config should be valid JSON on disk."""
    target = tmp_path / "MID360s_config.json"
    render_mid360s_config("192.168.1.50", "192.168.1.191", target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["lidar_summary_info"]["lidar_type"] == 8
    assert "Mid360s" in loaded
    assert "MID360" not in loaded
