"""数据来源状态：simulation / replay 运行中，live_pending 预留（不虚构）。"""

from fastapi import APIRouter

from ..provenance import make_provenance
from ..utils import utcnow_iso

router = APIRouter()


@router.get("/data-sources")
def list_data_sources() -> list[dict]:
    now = utcnow_iso()
    return [
        {
            "mode": "simulation",
            "name": "内置模拟器",
            "status": "active",
            "last_update": now,
            "message": None,
            "source_type": "simulation",
            "provenance": make_provenance("simulation", "simulated"),
        },
        {
            "mode": "replay",
            "name": "历史数据回放",
            "status": "active",
            "last_update": now,
            "message": None,
            "source_type": "replay",
            "provenance": make_provenance("replay", "simulated"),
        },
        {
            "mode": "live_pending",
            "name": "甲方实时数据接入",
            "status": "reserved",
            "last_update": None,
            "message": "预留接口，待甲方协议确认后启用",
            "source_type": "live_pending",
            "provenance": make_provenance("live_pending", "pending_confirmation"),
        },
    ]
