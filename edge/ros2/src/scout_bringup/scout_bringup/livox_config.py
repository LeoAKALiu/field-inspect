"""Render Livox Mid-360S JSON config from unified system parameters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_mid360s_config(host_ip: str, lidar_ip: str) -> dict[str, Any]:
    """Build the MID360s_config.json payload for Livox SDK."""
    return {
        "lidar_summary_info": {"lidar_type": 8},
        "Mid360s": {
            "lidar_net_info": {
                "cmd_data_port": 56100,
                "push_msg_port": 56200,
                "point_data_port": 56300,
                "imu_data_port": 56400,
                "log_data_port": 56500,
            },
            "host_net_info": [
                {
                    "host_ip": host_ip,
                    "cmd_data_port": 56101,
                    "push_msg_port": 56201,
                    "point_data_port": 56301,
                    "imu_data_port": 56401,
                    "log_data_port": 56501,
                }
            ],
        },
        "lidar_configs": [
            {
                "ip": lidar_ip,
                "pcl_data_type": 1,
                "pattern_mode": 0,
                "extrinsic_parameter": {
                    "roll": 0.0,
                    "pitch": 0.0,
                    "yaw": 0.0,
                    "x": 0,
                    "y": 0,
                    "z": 0,
                },
            }
        ],
    }


def render_mid360s_config(host_ip: str, lidar_ip: str, output_path: Path) -> Path:
    """Write MID360s_config.json to ``output_path`` and return the path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_mid360s_config(host_ip, lidar_ip)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path
