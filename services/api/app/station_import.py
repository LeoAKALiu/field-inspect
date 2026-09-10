"""Materialize indexed vehicle route outcomes without claiming detector confirmation."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import uuid
from pathlib import PurePosixPath

from .domain_contract import StationAttempt
from .instruments import instant, save

ARTIFACT = "artifacts/stations/attempts.json"


def prepare(manifest, archive):
    # Legacy packages have no formal inventory and remain explicitly unindexed.
    inventory = {item.path: item.sha256 for item in getattr(manifest, "payload_inventory", [])}
    if ARTIFACT not in inventory:
        return []
    root = archive / "data"

    def checked(path):
        parts = PurePosixPath(path)
        if parts.is_absolute() or ".." in parts.parts or "\\" in path or ":" in path or path not in inventory:
            raise ValueError("station source must be an indexed relative payload")
        location = root.joinpath(*parts.parts)
        if location.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("station JSON exceeds 16 MiB")
        raw = location.read_bytes()
        if hashlib.sha256(raw).hexdigest() != inventory[path]:
            raise ValueError("station evidence digest mismatch")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("station metadata must be a JSON object")
        return value

    document = checked(ARTIFACT)
    if (document.get("schema_version") != "1.0" or
            document.get("artifact_kind") != "vehicle_station_attempts" or
            document.get("run_id") != manifest.run_id):
        raise ValueError("station artifact identity/version mismatch")
    operational = checked("metadata/jetson-manifest.json")
    reference = operational.get("station_attempts", {})
    if not isinstance(reference, dict):
        raise ValueError("station operational index must be an object")
    if (reference.get("path") != ARTIFACT or reference.get("sha256") != inventory[ARTIFACT]
            or reference.get("generated_at") != document.get("generated_at")
            or reference.get("producer_version") != document.get("producer_version")):
        raise ValueError("station operational index mismatch")
    captures = {}
    for path in sorted(inventory):
        if not path.startswith("artifacts/station-captures/") or PurePosixPath(path).name not in {"started.json", "captured.json", "failed.json"}:
            continue
        capture = checked(path)
        key = (capture["run_id"], capture["execution_id"], capture["station_id"])
        identity = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]
        seq = capture["attempt_seq"]
        if type(seq) is not int or seq < 1 or key[0] != manifest.run_id:
            raise ValueError("capture identity/sequence mismatch")
        directory = f"artifacts/station-captures/{identity}/{seq}"
        if path != f"{directory}/{capture['state']}.json":
            raise ValueError("capture path/state mismatch")
        if capture["state"] == "captured":
            if (capture.get("image_path") != "frame.png" or not capture.get("image_sha256")
                    or inventory.get(directory + "/frame.png") != capture["image_sha256"]
                    or directory + "/source.json" not in inventory):
                raise ValueError("capture image/source digest missing or mismatched")
        elif capture.get("image_path") is not None or capture.get("image_sha256") is not None:
            raise ValueError("unfinished capture cannot claim an image")
        captures.setdefault(key, []).append({"capture_attempt_seq": seq, "state": capture["state"],
            "reason": capture["reason"], "recorded_at_ns": capture["recorded_at_ns"],
            "artifact_path": path, "artifact_sha256": inventory[path], "image_sha256": capture.get("image_sha256")})
    for events in captures.values():
        by_sequence = {}
        for event in events:
            by_sequence.setdefault(event["capture_attempt_seq"], set()).add(event["state"])
        if any("started" not in states or {"captured", "failed"} <= states for states in by_sequence.values()):
            raise ValueError("capture lifecycle is missing start or has conflicting terminal states")
    if document.get("run_status") != manifest.status:
        raise ValueError("station run status contradicts package")
    if not isinstance(document.get("source_files"), list) or not document["source_files"]:
        raise ValueError("station source inventory is empty")
    for source in document["source_files"]:
        if not isinstance(source, dict):
            raise ValueError("station source must be an object")
        if inventory.get(source["path"]) != source["sha256"]:
            raise ValueError("station source digest is not in package inventory")
    start, end = instant(str(manifest.started_at)), instant(str(manifest.ended_at))
    rows, identities, sequences = [], set(), set()
    attempts = document["attempts"]
    if not isinstance(attempts, list) or len(attempts) > 10000:
        raise ValueError("invalid station attempt list")
    source_type = "replay" if manifest.source_kind == "inspection_run" else "simulation"
    for raw in attempts:
        if not isinstance(raw, dict) or not isinstance(raw.get("mcap_range"), dict):
            raise ValueError("station attempt and MCAP bounds must be objects")
        if raw["run_id"] != manifest.run_id or not raw["execution_id"]:
            raise ValueError("station attempt run/execution mismatch")
        if raw["attempt_id"] != uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([manifest.run_id, raw["execution_id"], raw["station_id"]])).hex:
            raise ValueError("station attempt ID is not derived from its identity")
        bounds = raw["mcap_range"]
        if bounds.get("bag_path") != f"bag/{manifest.run_id}":
            raise ValueError("station MCAP bag reference mismatch")
        if bounds["clock"] != "rosbag_receive_time":
            raise ValueError("unsupported station clock")
        times = []
        for field in ("start_ns", "end_ns"):
            value = bounds[field]
            if type(value) is not int or value < 0:
                raise ValueError("invalid station nanosecond timestamp")
            seconds, nanos = divmod(value, 1000000000)
            times.append(datetime.fromtimestamp(seconds, timezone.utc) + timedelta(microseconds=nanos // 1000))
        if not start <= times[0] <= times[1] <= end:
            raise ValueError("station outcome lies outside run interval")
        model = StationAttempt.model_validate({
            "contract_version": "0.3", **{k: raw[k] for k in
                ("attempt_id", "run_id", "station_id", "attempt_seq", "result", "reason_code")},
            "started_at": times[0].isoformat(), "ended_at": times[1].isoformat(),
            "reason_detail": "Route outcome; interval begins at execution start, not station arrival. Image capture is separate.",
            "source_type": source_type,
            "provenance": {"source": source_type, "status": "pending_confirmation" if source_type == "replay" else "simulated"},
            "lineage": {"source": source_type, "producer": document["producer"],
                        "producer_version": document["producer_version"],
                        "derived_from": ARTIFACT + "#sha256=" + inventory[ARTIFACT]},
        }).model_dump(mode="json")
        key = (model["station_id"], model["attempt_seq"])
        if model["attempt_id"] in identities or key in sequences:
            raise ValueError("duplicate station attempt identity/sequence")
        identities.add(model["attempt_id"]); sequences.add(key)
        rows.append(("station_attempt", model["attempt_id"], model))
        rows.append(("station_evidence", model["attempt_id"], {
            "attempt_id": model["attempt_id"], "run_id": manifest.run_id,
            "execution_id": raw["execution_id"], "station_id": raw["station_id"],
            "mcap_range": bounds, "artifact_path": ARTIFACT,
            "captures": captures.get((manifest.run_id, raw["execution_id"], raw["station_id"]), []),
            "artifact_sha256": inventory[ARTIFACT],
            "verification": "package_hashes_and_structure; MCAP outcome not independently recomputed on server",
        }))
    return rows


def materialize(db, conn, run_id, rows):
    for kind, identity, payload in rows:
        save(db, kind, identity, run_id, payload, "package_import", conn=conn)


def reindex(db, run_id):
    """Explicit idempotent backfill; archive integrity is checked, no MCAP replay."""
    import bagit
    from pathlib import Path
    from . import run_bundle as bundle
    from .writer_lock import ImportWriterLock

    row = db.query_one("SELECT * FROM run_bundle_imports WHERE run_id=?", (run_id,))
    if row is None:
        raise ValueError("unknown archived run")
    archive = Path(row["archive_path"])
    with ImportWriterLock(archive.parent.parent):
        bundle._assert_plain_tree(archive)
        bag = bagit.Bag(str(archive))
        bag.validate()
        manifest, _ = bundle._validate_semantics(archive, bag)
        if manifest.run_id != run_id or bundle._bundle_hash(bag) != row["bundle_sha256"]:
            raise ValueError("archived package no longer matches import ledger")
        rows = prepare(manifest, archive)
        with db.transaction() as conn:
            materialize(db, conn, run_id, rows)
    return {"run_id": run_id, "station_attempts": sum(kind == "station_attempt" for kind, _, _ in rows),
            "status": "indexed" if rows else "no_indexed_station_artifact"}
