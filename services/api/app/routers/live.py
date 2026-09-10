"""live 实时数据接入：预留接口，恒 501（不虚构真实接入）。"""

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class LiveIngestEnvelope(BaseModel):
    """预留报文信封，字段映射待甲方协议确认（整体 pending_confirmation）。"""

    source_id: str
    timestamp: datetime
    payload: dict


@router.post("/live/ingest")
def ingest_live(body: LiveIngestEnvelope) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "code": "not_implemented",
                "message": "live 实时数据接入为预留接口，待甲方协议确认后启用",
            }
        },
    )
