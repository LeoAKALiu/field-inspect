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


RecordKind = Literal["observation", "localization", "asset_match", "station_attempt", "reading", "match_review", "station_evidence", "processing_report"]


@router.get("/pages/{kind}")
def record_page(kind: RecordKind, run_id: str | None = None, attempt_id: str | None = None,
                cursor: str = Query(default="", max_length=512), limit: int = Query(default=50, ge=1, le=100),
                db: Database = Depends(get_db)):
    sql = "SELECT id,payload FROM instrument_records WHERE kind=? AND id>?"
    args = [kind,cursor]
    if run_id is not None:
        sql += " AND run_id=?"; args.append(run_id)
    if attempt_id is not None:
        sql += " AND json_extract(payload,'$.attempt_id')=?"; args.append(attempt_id)
    rows = db.query_all(sql + " ORDER BY id LIMIT ?", tuple(args + [limit+1]))
    return {"items":[json.loads(row["payload"]) for row in rows[:limit]],
            "next_cursor":rows[limit-1]["id"] if len(rows)>limit else None}


@router.get("/records/{kind}/{identity}")
def get_record(kind: RecordKind, identity: str, db: Database = Depends(get_db)):
    return call(service.record, db, kind, identity)


@router.post("/runs/{run_id}/jobs", status_code=202)
def create_job(run_id: str, db: Database = Depends(get_db)):
    from ..processing_jobs import create
    return call(create, db, run_id)


@router.get("/jobs")
def list_jobs(run_id: str, cursor: str = Query(default="", max_length=128),
              limit: int = Query(default=20, ge=1, le=100), db: Database = Depends(get_db)):
    from ..processing_jobs import describe
    rows = db.query_all("SELECT id FROM processing_jobs WHERE run_id=? AND id>? ORDER BY id LIMIT ?", (run_id,cursor,limit+1))
    return {"items":[describe(db,row["id"]) for row in rows[:limit]], "next_cursor":rows[limit-1]["id"] if len(rows)>limit else None}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Database = Depends(get_db)):
    from ..processing_jobs import describe
    return call(describe, db, job_id)


@router.post("/jobs/{job_id}/{action}")
def control_job(job_id: str, action: Literal["pause", "resume", "retry_failed"], db: Database = Depends(get_db)):
    from ..processing_jobs import control
    return call(control, db, job_id, action)


@router.get("/jobs/{job_id}/items")
def job_items(job_id: str, cursor: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
              db: Database = Depends(get_db)):
    rows = db.query_all("SELECT * FROM processing_job_items WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?", (job_id,cursor,limit+1))
    return {"items":[dict(row) for row in rows[:limit]], "next_cursor":rows[limit-1]["seq"] if len(rows)>limit else None}


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, cursor: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
               db: Database = Depends(get_db)):
    rows = db.query_all("SELECT * FROM processing_job_events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?", (job_id,cursor,limit+1))
    return {"items":[dict(row) for row in rows[:limit]], "next_cursor":rows[limit-1]["seq"] if len(rows)>limit else None}
