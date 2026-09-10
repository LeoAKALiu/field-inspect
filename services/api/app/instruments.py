"""Persist the existing v0.3 objects and compute reproducible reading associations."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from .domain_contract import (AssetMatch, InstrumentLocalization, InstrumentObservation,
                              InstrumentReading, Position3D, StationAttempt, StrictModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS instrument_assets (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instrument_records (
 kind TEXT NOT NULL, id TEXT NOT NULL, run_id TEXT NOT NULL,
 payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(kind,id)
);
CREATE INDEX IF NOT EXISTS instrument_run_idx ON instrument_records(run_id,kind);
CREATE TABLE IF NOT EXISTS instrument_audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
 object_id TEXT NOT NULL, sha256 TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class Asset(StrictModel):
    asset_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    scene_version_id: str = Field(min_length=1)
    position: Position3D
    marker_family: str = Field(min_length=1)
    marker_id: int = Field(ge=0)
    marker_role: Literal["instrument_proxy"]
    match_tolerance_m: float = Field(gt=0)
    units: dict[Literal["deep_base", "shallow_base", "delta"], str]
    thresholds: dict[Literal["deep_base", "shallow_base", "delta"], dict[str, float]] = {}
    rule_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_units_rules(self):
        if set(self.units) != {"deep_base", "shallow_base", "delta"} or not all(self.units.values()):
            raise ValueError("all three channel units must be explicitly configured")
        for limits in self.thresholds.values():
            if set(limits) != {"attention", "alarm"} or limits["attention"] >= limits["alarm"]:
                raise ValueError("thresholds require attention < alarm")
        return self


class ReadingInput(StrictModel):
    reading: InstrumentReading
    mapping_version: str = Field(min_length=1)
    source_timezone: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    raw_fields: dict


class HistoryMapping(StrictModel):
    mapping_version: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    timestamp_field: str = Field(min_length=1)
    asset_field: str = Field(min_length=1)
    asset_ids: dict[str, str]
    fields: dict[Literal["deep_base", "shallow_base", "delta"], str]
    units: dict[Literal["deep_base", "shallow_base", "delta"], str]
    validity_seconds: int = Field(gt=0, le=365*86400)
    quality_field: str = Field(min_length=1)
    quality_values: dict[str, Literal["good", "uncertain", "bad"]]


class HistoryBatch(StrictModel):
    run_id: str = Field(min_length=1)
    mapping: HistoryMapping
    source_type: Literal["replay", "simulation"]
    rows: list[dict] = Field(min_length=1, max_length=10000)


class MatchReview(StrictModel):
    review_id: str = Field(min_length=1)
    match_id: str = Field(min_length=1)
    asset_id: str | None
    operator_label: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)
    expected_review_id: str | None = None


class MatchInput(StrictModel):
    localization_id: str
    marker_family: str
    marker_id: int = Field(ge=0)


MODELS = {"observation": (InstrumentObservation, "observation_id"),
          "localization": (InstrumentLocalization, "localization_id"),
          "station_attempt": (StationAttempt, "attempt_id")}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps require an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _audit(conn, action, object_id, digest, actor):
    from .access import audit_actor
    actor = audit_actor(actor)
    conn.execute("INSERT INTO instrument_audit(action,object_id,sha256,actor,created_at) VALUES(?,?,?,?,?)",
                 (action, object_id, digest, actor, datetime.now(timezone.utc).isoformat()))


def record(db, kind, identity):
    row = db.query_one("SELECT payload FROM instrument_records WHERE kind=? AND id=?", (kind, identity))
    if row is None: raise ValueError(f"unknown {kind} reference")
    return json.loads(row["payload"])


def records(db, kind, run_id=None):
    sql = "SELECT payload FROM instrument_records WHERE kind=?"
    args = (kind,)
    if run_id is not None:
        sql += " AND run_id=?"; args += (run_id,)
    return [json.loads(r["payload"]) for r in db.query_all(sql + " ORDER BY id", args)]


def save(db, kind, identity, run_id, payload, actor, conn=None):
    if conn is None:
        with db.transaction() as transaction:
            return save(db, kind, identity, run_id, payload, actor, transaction)
    text = encoded(payload); digest = hashlib.sha256(text.encode()).hexdigest()
    existing = conn.execute("SELECT sha256 FROM instrument_records WHERE kind=? AND id=?", (kind, identity)).fetchone()
    if existing:
        if existing["sha256"] != digest: raise ValueError("immutable ID already has different content")
        return payload
    conn.execute("INSERT INTO instrument_records VALUES(?,?,?,?,?)", (kind, identity, run_id, text, digest))
    _audit(conn, kind, identity, digest, actor)
    return payload


def save_asset(db, asset: Asset, actor):
    text = encoded(asset.model_dump())
    with db.transaction() as conn:
        existing = conn.execute("SELECT payload FROM instrument_assets WHERE id=?", (asset.asset_id,)).fetchone()
        if existing:
            if existing["payload"] != text: raise ValueError("asset registration is immutable; use a new version ID")
            return asset.model_dump()
        if conn.execute("SELECT 1 FROM registered_landmarks l JOIN verified_alignments a ON a.alignment_id=l.alignment_id WHERE a.scene_version_id=? AND l.dictionary=? AND l.marker_id=?", (asset.scene_version_id, asset.marker_family, asset.marker_id)).fetchone():
            raise ValueError("scene acceptance landmark cannot be reused as instrument identity")
        # Duplicate proxies remain unconfirmed rather than becoming ambiguous automatic matches.
        for row in conn.execute("SELECT payload FROM instrument_assets"):
            other = json.loads(row["payload"])
            if (other["scene_version_id"], other["marker_family"], other["marker_id"]) == (asset.scene_version_id, asset.marker_family, asset.marker_id):
                raise ValueError("duplicate registered marker within scene version")
        if conn.execute("SELECT 1 FROM scene_versions WHERE scene_version_id=?", (asset.scene_version_id,)).fetchone() is None:
            raise ValueError("asset requires a registered scene version")
        conn.execute("INSERT INTO instrument_assets VALUES(?,?)", (asset.asset_id, text))
        _audit(conn, "asset_registration", asset.asset_id, hashlib.sha256(text.encode()).hexdigest(), actor)
    return asset.model_dump()


def save_object(db, kind, raw, actor):
    if kind not in MODELS: raise ValueError("unsupported v0.3 object kind")
    model, id_field = MODELS[kind]
    value = model.model_validate(raw).model_dump()
    run = db.query_one("SELECT * FROM tasks WHERE id=?", (value["run_id"],))
    if run is None: raise ValueError("unknown imported run")
    if run["run_kind"] == "recorded" and (value.get("source_type") != "replay" or value["lineage"]["source"] != "replay" or value["provenance"]["source"] != "replay"):
        raise ValueError("simulation cannot be attached to a recorded run")
    if value.get("source_type") == "live_pending": raise ValueError("live input is not an offline observation")
    if kind == "observation":
        captured = instant(value["captured_at"])
        if not run["actual_start"] or not run["actual_end"] or not instant(run["actual_start"]) <= captured <= instant(run["actual_end"]):
            raise ValueError("observation time is outside the run")
    if kind == "localization":
        observation = record(db, "observation", value["observation_id"])
        if observation["run_id"] != value["run_id"] or observation["source_type"] != value["source_type"]:
            raise ValueError("localization run/source reference mismatch")
    if kind == "station_attempt" and instant(value["ended_at"]) < instant(value["started_at"]):
        raise ValueError("station attempt end precedes start")
    return save(db, kind, value[id_field], value["run_id"], value, actor)


def match_asset(db, request: MatchInput, actor):
    local = record(db, "localization", request.localization_id)
    observation = record(db, "observation", local["observation_id"])
    run = db.query_one("SELECT scene_version_id FROM tasks WHERE id=?", (local["run_id"],))
    candidates = [json.loads(r["payload"]) for r in db.query_all("SELECT payload FROM instrument_assets")]
    candidates = [a for a in candidates if (a["scene_version_id"], a["marker_family"], a["marker_id"]) ==
                  (run["scene_version_id"], request.marker_family, request.marker_id)]
    status, asset_id = "unmatched", None
    if candidates:
        status = "needs_review"
        asset = candidates[0]
        if len(candidates) == 1 and local["status"] == "matched" and observation["confidence"] >= 0.8 and local["position"] and local["residual_m"] is not None and 0 <= local["residual_m"] <= asset["match_tolerance_m"]:
            distance = math.sqrt(sum((local["position"][k]-asset["position"][k])**2 for k in ("x", "y", "z")))
            if distance <= asset["match_tolerance_m"]:
                status, asset_id = "matched", asset["asset_id"]
    raw = {"contract_version": "0.3", "match_id": "match-" + hashlib.sha256(encoded({"input": request.model_dump(), "candidates": candidates, "rule": "marker-space-confidence-v1"}).encode()).hexdigest()[:24],
        "run_id": local["run_id"], "localization_id": request.localization_id,
        "status": status, "asset_id": asset_id,
        "candidate_asset_ids": [a["asset_id"] for a in candidates] if status == "needs_review" else [],
        "rule_version": "marker-space-confidence-v1", "source_type": observation["source_type"],
        "provenance": {"source": observation["source_type"], "status": "pending_confirmation"},
        "lineage": {"source": observation["lineage"]["source"], "producer": "astra-inspect",
            "producer_version": "0.1.0", "derived_from": request.localization_id}}
    value = AssetMatch.model_validate(raw).model_dump()
    return save(db, "asset_match", value["match_id"], value["run_id"], value, actor)


def review_match(db, request: MatchReview):
    with db.transaction() as conn:
        match = record(db, "asset_match", request.match_id)
        if request.asset_id is not None:
            row = db.query_one("SELECT payload FROM instrument_assets WHERE id=?", (request.asset_id,))
            if row is None: raise ValueError("review must refer to a registered asset")
            run = db.query_one("SELECT scene_version_id FROM tasks WHERE id=?", (match["run_id"],))
            if json.loads(row["payload"])["scene_version_id"] != run["scene_version_id"]:
                raise ValueError("review asset belongs to another scene version")
        value = request.model_dump(); value["run_id"] = match["run_id"]
        existing = conn.execute("SELECT payload FROM instrument_records WHERE kind='match_review' AND id=?", (request.review_id,)).fetchone()
        if existing:
            previous = json.loads(existing["payload"])
            if any(previous.get(k) != v for k,v in value.items()): raise ValueError("review ID conflict")
            return previous
        latest = conn.execute("SELECT id FROM instrument_records WHERE kind='match_review' AND json_extract(payload,'$.match_id')=? ORDER BY rowid DESC LIMIT 1", (request.match_id,)).fetchone()
        if (latest["id"] if latest else None) != request.expected_review_id:
            raise ValueError("review changed; refresh the current decision before saving")
        value["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        return save(db, "match_review", request.review_id, match["run_id"], value, request.operator_label, conn=conn)


def save_reading(db, payload: ReadingInput, actor):
    from zoneinfo import ZoneInfo
    ZoneInfo(payload.source_timezone)
    reading = payload.reading
    row = db.query_one("SELECT payload FROM instrument_assets WHERE id=?", (reading.asset_id,))
    if row is None: raise ValueError("unknown registered asset")
    asset = json.loads(row["payload"])
    run = db.query_one("SELECT * FROM tasks WHERE id=?", (reading.run_id,))
    if not run:
        raise ValueError("reading association requires a known run")
    if run["scene_version_id"] != asset["scene_version_id"]:
        raise ValueError("reading asset and run scene versions disagree")
    if run["run_kind"] == "recorded" and (reading.source_type != "replay" or reading.lineage.source != "replay"):
        raise ValueError("synthetic reading cannot be attached to a recorded run")
    captured = instant(reading.captured_at)
    if reading.valid_until and instant(reading.valid_until) < captured:
        raise ValueError("valid_until precedes reading time")
    if set(m.name for m in reading.metrics) != {"deep_base", "shallow_base", "delta"}:
        raise ValueError("mapped reading must contain all three named channels")
    if any(m.unit != asset["units"][m.name] for m in reading.metrics):
        raise ValueError("reading units disagree with explicit asset mapping")
    value = payload.model_dump()
    with db.transaction() as conn:
        for item in conn.execute("SELECT payload FROM instrument_records WHERE kind='reading'"):
            prior = json.loads(item["payload"])["reading"]
            if prior["asset_id"] == reading.asset_id and instant(prior["captured_at"]) == captured and prior["reading_id"] != reading.reading_id:
                raise ValueError("duplicate asset timestamp requires review; no silent overwrite")
        return save(db, "reading", reading.reading_id, reading.run_id, value, actor, conn)


def analyze(db, asset_id, at):
    moment = instant(at)
    row = db.query_one("SELECT payload FROM instrument_assets WHERE id=?", (asset_id,))
    if row is None: raise ValueError("unknown asset")
    asset = json.loads(row["payload"])
    history = [x for x in records(db, "reading") if x["reading"]["asset_id"] == asset_id]
    past = sorted([x for x in history if instant(x["reading"]["captured_at"]) <= moment],
                  key=lambda x: instant(x["reading"]["captured_at"]))
    response = {"asset_id": asset_id, "asset": asset, "at": at, "rule_version": asset["rule_version"],
                "recommendation": "review", "status": "missing", "reading": None,
                "channels": [], "history": past, "excluded": []}
    response["excluded"] = [{"reading_id": x["reading"]["reading_id"], "reason": "future"}
                            for x in history if instant(x["reading"]["captured_at"]) > moment]
    valid = []
    for item in past:
        reading = item["reading"]
        reason = ("invalid" if reading["quality"] != "good" else
                  "expiry_unconfigured" if reading["valid_until"] is None else
                  "expired" if instant(reading["valid_until"]) < moment else None)
        if reason:
            response["excluded"].append({"reading_id": reading["reading_id"], "reason": reason})
        else: valid.append(item)
    if not valid:
        response["status"] = "no_valid_prior_reading" if past else "future_only" if history else "missing"
        return response
    selected = valid[-1]; response["reading"] = selected
    response["status"] = "available"
    priorities = {"normal": 0, "attention": 1, "review": 2, "alarm": 3}
    for metric in selected["reading"]["metrics"]:
        limits = asset["thresholds"].get(metric["name"])
        recommendation = "review" if limits is None else "alarm" if metric["value"] >= limits["alarm"] else "attention" if metric["value"] >= limits["attention"] else "normal"
        response["channels"].append({**metric, "recommendation": recommendation,
                                    "thresholds": limits, "threshold_status": "configured" if limits else "unconfigured"})
    response["recommendation"] = max((c["recommendation"] for c in response["channels"]), key=priorities.get)
    response["input_reading_id"] = selected["reading"]["reading_id"]
    response["mapping_version"] = selected["mapping_version"]
    response["trend"] = []
    if len(valid) >= 2:
        before = valid[-2]["reading"]
        elapsed = (instant(selected["reading"]["captured_at"])-instant(before["captured_at"])).total_seconds()
        previous_metrics = {m["name"]: m for m in before["metrics"]}
        for metric in selected["reading"]["metrics"]:
            prior = previous_metrics[metric["name"]]
            response["trend"].append({"channel": metric["name"], "change": metric["value"]-prior["value"],
                "unit": metric["unit"], "per_day": (metric["value"]-prior["value"])*86400/elapsed,
                "from_reading_id": before["reading_id"], "to_reading_id": selected["reading"]["reading_id"]})
    response["notice"] = "规则建议供人员复核，不构成工程安全结论。"
    return response


def import_history(db, batch: HistoryBatch, actor):
    """Persist a versioned explicit mapping; report each row without hiding rejects."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    mapping = batch.mapping
    zone = ZoneInfo(mapping.timezone)
    if set(mapping.fields) != {"deep_base", "shallow_base", "delta"} or set(mapping.units) != set(mapping.fields):
        raise ValueError("mapping must explicitly name all three channels and units")
    if len(set(mapping.fields.values())) != 3:
        raise ValueError("each channel needs its own source field; delta is not inferred")
    save(db, "mapping", mapping.mapping_version, "", mapping.model_dump(), actor)
    outcomes = []
    for index, raw in enumerate(batch.rows):
        try:
            parsed = datetime.fromisoformat(str(raw[mapping.timestamp_field]).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                early, late = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
                if early.utcoffset() != late.utcoffset():
                    raise ValueError("ambiguous/nonexistent local time requires explicit offset")
                parsed = early
            parsed = parsed.astimezone(timezone.utc)
            asset_id = mapping.asset_ids[str(raw[mapping.asset_field])]
            quality = mapping.quality_values[str(raw[mapping.quality_field])]
            metrics = []
            for channel, source_field in mapping.fields.items():
                if isinstance(raw[source_field], bool): raise ValueError("boolean is not a reading")
                metrics.append({"name": channel, "value": float(raw[source_field]), "unit": mapping.units[channel]})
            identity = hashlib.sha256(encoded({"mapping": mapping.mapping_version, "raw": raw,
                                               "run_id": batch.run_id}).encode()).hexdigest()
            source = batch.source_type
            value = {"contract_version": "0.3", "reading_id": "reading-"+identity[:32],
                "run_id": batch.run_id, "asset_id": asset_id, "captured_at": parsed.isoformat(),
                "metrics": metrics, "quality": quality,
                "valid_until": (parsed+timedelta(seconds=mapping.validity_seconds)).isoformat(),
                "source_type": source, "provenance": {"source": source, "status": "pending_confirmation" if source == "replay" else "simulated"},
                "lineage": {"source": source, "producer": "astra-inspect-history-import",
                    "producer_version": "0.1.0", "derived_from": mapping.source_reference}}
            result = save_reading(db, ReadingInput(reading=value, mapping_version=mapping.mapping_version,
                source_timezone=mapping.timezone, source_reference=mapping.source_reference, raw_fields=raw), actor)
            outcomes.append({"row": index, "status": "saved", "reading_id": result["reading"]["reading_id"]})
        except (KeyError, ValueError, OverflowError) as exc:
            outcomes.append({"row": index, "status": "rejected", "reason": str(exc)})
    return {"mapping_version": mapping.mapping_version, "atomicity": "per_row", "rows": outcomes,
            "saved": sum(x["status"] == "saved" for x in outcomes),
            "rejected": sum(x["status"] == "rejected" for x in outcomes)}
