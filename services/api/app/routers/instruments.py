"""Private instrument registry, observations, and three-channel analysis API."""
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import instruments as service
from ..db import Database
from . import get_db

router = APIRouter(prefix="/instruments", tags=["Instruments"])


def call(operation, *args):
    try:
        return operation(*args)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/assets")
def assets(db: Database = Depends(get_db)):
    return [json.loads(row["payload"]) for row in db.query_all("SELECT payload FROM instrument_assets ORDER BY id")]


@router.post("/assets")
def register(asset: service.Asset, db: Database = Depends(get_db)):
    return call(service.save_asset, db, asset, "authorized_operator")


@router.get("/records/{kind}")
def list_records(kind: Literal["observation", "localization", "asset_match", "station_attempt", "reading", "match_review", "station_evidence", "processing_report"],
                 run_id: str | None = None, db: Database = Depends(get_db)):
    return service.records(db, kind, run_id)


@router.post("/records/{kind}")
def put_record(kind: Literal["observation", "localization", "station_attempt"],
               payload: dict = Body(), db: Database = Depends(get_db)):
    return call(service.save_object, db, kind, payload, "authorized_operator")


@router.post("/matches")
def match(payload: service.MatchInput, db: Database = Depends(get_db)):
    return call(service.match_asset, db, payload, "authorized_operator")


@router.post("/reviews")
def review(payload: service.MatchReview, db: Database = Depends(get_db)):
    return call(service.review_match, db, payload)


@router.post("/readings")
def reading(payload: service.ReadingInput, db: Database = Depends(get_db)):
    return call(service.save_reading, db, payload, "authorized_operator")


@router.post("/history")
def history(payload: service.HistoryBatch, db: Database = Depends(get_db)):
    return call(service.import_history, db, payload, "authorized_operator")


@router.get("/assets/{asset_id}/analysis")
def analysis(asset_id: str, at: str | None = Query(default=None), db: Database = Depends(get_db)):
    return call(service.analyze, db, asset_id, at or datetime.now(timezone.utc).isoformat())


@router.get("/observations/{observation_id}/analysis")
def observed_analysis(observation_id: str, db: Database = Depends(get_db)):
    observation = call(service.record, db, "observation", observation_id)
    matches = [m for m in service.records(db, "asset_match", observation["run_id"])
               if call(service.record, db, "localization", m["localization_id"])["observation_id"] == observation_id]
    reviews = service.records(db, "match_review", observation["run_id"])
    confirmed = set()
    for match in matches:
        relevant = sorted([r for r in reviews if r["match_id"] == match["match_id"]], key=lambda r: r["reviewed_at"])
        if relevant:
            if relevant[-1]["asset_id"]: confirmed.add(relevant[-1]["asset_id"])
        elif match["status"] == "matched": confirmed.add(match["asset_id"])
    if len(confirmed) != 1:
        return {"observation_id": observation_id, "status": "needs_review" if matches else "unmatched", "matches": matches}
    return call(service.analyze, db, confirmed.pop(), observation["captured_at"])


@router.post("/runs/{run_id}/reindex-stations")
def reindex_stations(run_id: str, db: Database = Depends(get_db)):
    import bagit
    from ..station_import import reindex
    from ..writer_lock import WriterLockHeld
    try:
        return reindex(db, run_id)
    except WriterLockHeld as exc:
        raise HTTPException(409, "Import writer is busy") from exc
    except (ValueError, KeyError, TypeError, OSError, OverflowError, bagit.BagError) as exc:
        raise HTTPException(422, "Station archive cannot be indexed; review server archive integrity and station metadata") from exc


@router.post("/stations/{attempt_id}/captures/{sequence}/process")
def process_capture(attempt_id: str, sequence: int, db: Database = Depends(get_db)):
    import bagit
    from ..station_processing import process
    from ..writer_lock import WriterLockHeld
    if sequence < 1:
        raise HTTPException(422, "Capture sequence must be positive")
    try:
        return process(db, attempt_id, sequence)
    except WriterLockHeld as exc:
        raise HTTPException(409, "Import writer is busy") from exc
    except ImportError as exc:
        raise HTTPException(503, "Install the locked server image processing dependencies") from exc
    except (ValueError, KeyError, TypeError, OSError, OverflowError, bagit.BagError) as exc:
        raise HTTPException(422, str(exc)) from exc
