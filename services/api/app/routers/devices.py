"""传感器设备与确定性模拟读数曲线（正弦 + 定种子噪声，不落库）。"""

import hashlib
import math
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query

from ..db import Database, device_to_dict
from ..errors import not_found, raise_error
from ..provenance import make_provenance
from ..utils import iso, parse_dt, utcnow
from . import get_db

router = APIRouter()

DeviceType = Literal[
    "delamination", "displacement", "convergence", "stress",
    "temperature", "humidity", "gas",
]

# 各类型曲线参数：(base, amplitude, period_sec, noise_amplitude)
TYPE_CURVES: dict[str, tuple[float, float, float, float]] = {
    "delamination": (0.8, 0.3, 86400.0, 0.02),
    "displacement": (1.5, 0.6, 43200.0, 0.05),
    "convergence": (2.0, 0.8, 86400.0, 0.05),
    "stress": (2.5, 0.8, 21600.0, 0.05),
    "temperature": (18.0, 4.0, 3600.0, 0.3),
    "humidity": (58.0, 9.0, 5400.0, 0.8),
    "gas": (4.5, 2.0, 2700.0, 0.2),
}


@router.get("/devices")
def list_devices(
    scene_id: str | None = None,
    type: DeviceType | None = None,
    db: Database = Depends(get_db),
) -> list[dict]:
    where, params = [], []
    if scene_id is not None:
        where.append("scene_id = ?")
        params.append(scene_id)
    if type is not None:
        where.append("type = ?")
        params.append(type)
    sql = "SELECT * FROM devices"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id ASC"
    return [device_to_dict(r) for r in db.query_all(sql, tuple(params))]


@router.get("/devices/{device_id}/readings")
def get_device_readings(
    device_id: str,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    interval_sec: int = Query(default=60, ge=1, le=3600),
    db: Database = Depends(get_db),
) -> list[dict]:
    row = db.query_one("SELECT * FROM devices WHERE id = ?", (device_id,))
    if row is None:
        not_found("设备", device_id)
    try:
        to_dt = parse_dt(to) if to else utcnow()
        from_dt = parse_dt(from_) if from_ else to_dt - timedelta(hours=1)
    except ValueError:
        raise_error(400, "bad_request", "from/to 必须是合法 ISO 8601 时间")
    if from_dt > to_dt:
        raise_error(400, "bad_request", "from 不能晚于 to")

    base, amp, period, noise_amp = TYPE_CURVES.get(row["type"], (10.0, 2.0, 3600.0, 0.2))
    # 每台设备在类型基准上叠加由 device_id 派生的固定偏移，保证设备间曲线不同
    jitter = int.from_bytes(hashlib.sha256(device_id.encode()).digest()[:4], "big") % 1000 / 1000.0
    base += jitter

    readings = []
    t = int(from_dt.timestamp())
    end = int(to_dt.timestamp())
    while t <= end:
        digest = hashlib.sha256(f"{device_id}:{t}".encode()).digest()
        n = int.from_bytes(digest[:8], "big") / 2**64  # [0, 1) 定种子噪声
        value = round(base + amp * math.sin(2 * math.pi * t / period) + (n * 2 - 1) * noise_amp, 4)
        readings.append(
            {
                "device_id": device_id,
                "timestamp": iso(datetime.fromtimestamp(t, UTC)),
                "value": value,
                "quality": "good" if n >= 0.02 else "uncertain",
                "source_type": "simulation",
                "provenance": make_provenance("simulation", "simulated"),
            }
        )
        t += interval_sec
    return readings
