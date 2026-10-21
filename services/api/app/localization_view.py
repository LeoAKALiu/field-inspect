"""Project persisted marker positions with their recorded capture and report identity."""
import hashlib
import json
from pathlib import PurePosixPath

from .domain_contract import InstrumentLocalization, InstrumentObservation
from .inspection_package import InspectionPackageManifest
from .instruments import encoded, instant, record
from .station_processing import VERSION


def page(db, run_id, cursor, limit):
    run = db.query_one("SELECT * FROM tasks WHERE id=?", (run_id,))
    ledger = db.query_one("SELECT * FROM run_bundle_imports WHERE run_id=?", (run_id,))
    if run is None or ledger is None or run["run_kind"] != "recorded":
        raise ValueError("marker positions require an imported recorded run")
    manifest = InspectionPackageManifest.model_validate_json(ledger["manifest_json"])
    if (manifest.run_id != run_id or manifest.source_kind != "inspection_run"
            or manifest.scene_version.scene_id != run["scene_id"]
            or manifest.scene_version.scene_version_id != run["scene_version_id"]
            or manifest.alignment.alignment_id != run["alignment_id"]):
        raise ValueError("marker positions do not match the run scene binding")
    inventory = {entry.path: entry.sha256 for entry in manifest.payload_inventory}
    rows = db.query_all("""
        SELECT l.id, l.payload AS localization, o.payload AS observation, p.payload AS report
        FROM instrument_records l
        JOIN instrument_records o ON o.kind='observation'
          AND o.id=json_extract(l.payload,'$.observation_id') AND o.run_id=l.run_id
        JOIN instrument_records p ON p.kind='processing_report'
          AND p.id=json_extract(l.payload,'$.lineage.derived_from') AND p.run_id=l.run_id
        WHERE l.kind='localization' AND l.run_id=? AND l.id>?
          AND json_extract(p.payload,'$.producer_version')=?
        ORDER BY l.id LIMIT ?
        """, (run_id, cursor, VERSION, limit + 1))

    def project(row):
        local = InstrumentLocalization.model_validate_json(row["localization"]).model_dump()
        observation = InstrumentObservation.model_validate_json(row["observation"]).model_dump()
        report = json.loads(row["report"])
        for value in (local, observation):
            if (value["run_id"] != run_id or value["source_type"] != "replay"
                    or value["lineage"]["source"] != "replay" or value["provenance"]["source"] != "replay"
                    or value["provenance"]["status"] != "pending_confirmation"
                    or value["lineage"]["derived_from"] != report["report_id"]
                    or value["lineage"]["producer"] != VERSION):
                raise ValueError("marker localization lineage mismatch")
        if (report["run_id"] != run_id or local["localization_id"] != row["id"]
                or local["observation_id"] != observation["observation_id"]
                or observation["calibration_ref"] != "sha256:" + inventory.get("config/camera_calibration.json", "")
                or not instant(str(manifest.started_at)) <= instant(observation["captured_at"]) <= instant(str(manifest.ended_at))):
            raise ValueError("marker observation run/time mismatch")
        inputs = {"package": ledger["bundle_sha256"], "attempt": report["attempt_id"],
                  "capture": report["capture_attempt_seq"], "version": VERSION}
        if (report["report_id"] != "processing-" + hashlib.sha256(encoded(inputs).encode()).hexdigest()[:24]
                or any(report["input_sha256"].get(key) != value for key, value in inputs.items())):
            raise ValueError("marker report does not match the imported package")
        evidence = record(db, "station_evidence", report["attempt_id"])
        captures = [capture for capture in evidence["captures"]
                    if capture["state"] == "captured" and capture["capture_attempt_seq"] == report["capture_attempt_seq"]]
        if evidence["run_id"] != run_id or evidence["attempt_id"] != report["attempt_id"] or len(captures) != 1:
            raise ValueError("marker capture identity mismatch")
        capture = captures[0]
        folder = PurePosixPath(capture["artifact_path"]).parent
        image = str(folder / "frame.png")
        if (inventory.get(capture["artifact_path"]) != capture["artifact_sha256"]
                or not capture.get("image_sha256") or inventory.get(image) != capture["image_sha256"]):
            raise ValueError("marker capture is not bound to the import inventory")
        required = [image, str(folder / "source.json"), "config/camera_calibration.json", "config/instrument-detector.json"]
        if local["position"] is not None:
            required += ["config/instrument-localization.json", "alignment/camera_to_scene.json", "config/scene_alignment.yaml"]
        if any(path not in inventory or report["input_sha256"].get(path) != inventory[path] for path in required):
            raise ValueError("marker report inputs differ from the import inventory")
        oid = observation["observation_id"]
        detections = [item for item in report["detections"] if item["observation_id"] == oid]
        estimates = [item for item in report["localization_estimates"] if item["observation_id"] == oid]
        if report["observations"].count(oid) != 1 or len(detections) != 1 or len(estimates) != 1:
            raise ValueError("marker observation has no unique report entry")
        detection, estimate = detections[0], estimates[0]
        if (estimate["marker_id"] != detection["marker_id"] or estimate.get("position") != local["position"]
                or (local["position"] is None and local["status"] != "unmatched")
                or (local["position"] is not None and (estimate["status"] != "estimated_needs_review" or local["status"] != "needs_review"))):
            raise ValueError("marker position differs from its processing report")
        return {"localization": local, "captured_at": observation["captured_at"],
                "dictionary": detection["dictionary"], "marker_id": detection["marker_id"],
                "attempt_id": report["attempt_id"], "capture_attempt_seq": report["capture_attempt_seq"],
                "report_id": report["report_id"], "image_sha256": capture["image_sha256"],
                "reason": estimate["reason"], "gaps": report["gaps"],
                "conditional_max_position_std_m": estimate.get("conditional_max_position_std_m"),
                "timing_displacement_bound_m": estimate.get("timing_displacement_bound_m")}

    return {"run_id": run_id, "scene_version_id": run["scene_version_id"], "alignment_id": run["alignment_id"],
            "items": [project(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}
