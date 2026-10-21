"""Synthetic database projections only; no detector or hardware evidence is executed."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.db import Database
from app.instruments import encoded, save
from app.localization_view import page
from app.station_processing import VERSION

RUN = "golden-run-v2-completed"


@pytest.fixture()
def markers():
    db = Database(":memory:")
    db.init_schema()
    manifest = json.loads((Path(__file__).resolve().parents[3] /
        "data/contract-fixtures/inspection-package-v2-completed/data/manifest.json").read_text())
    inventory = {entry["path"]: entry["sha256"] for entry in manifest["payload_inventory"]}
    folder = "artifacts/station-captures/fixture/1"
    paths = [f"{folder}/captured.json", f"{folder}/frame.png", f"{folder}/source.json",
             "config/instrument-detector.json", "config/instrument-localization.json", "config/scene_alignment.yaml"]
    for path in paths:
        inventory[path] = hashlib.sha256(path.encode()).hexdigest()
    manifest["payload_inventory"] = [{"path": path, "sha256": sha} for path, sha in inventory.items()]
    db.execute("INSERT INTO tasks(id,scene_id,name,mode,status,run_kind,scene_version_id,alignment_id) "
               "VALUES(?,?,?,'replay','completed','recorded',?,?)", (RUN, manifest["scene_version"]["scene_id"],
                   "Synthetic marker fixture", manifest["scene_version"]["scene_version_id"], manifest["alignment"]["alignment_id"]))
    db.execute("INSERT INTO run_bundle_imports "
               "(run_id,bundle_sha256,schema_version,source_kind,archive_path,manifest_json,imported_at) "
               "VALUES(?,?,?,?,?,?,?)", (RUN, "a" * 64, "2.0", "inspection_run", "/unused/synthetic/archive",
                                         json.dumps(manifest), "2026-10-21T00:00:00Z"))
    save(db, "station_evidence", "attempt-1", RUN, {"run_id": RUN, "attempt_id": "attempt-1", "captures": [
        {"capture_attempt_seq": 1, "state": "captured", "artifact_path": f"{folder}/captured.json",
         "artifact_sha256": inventory[f"{folder}/captured.json"], "image_sha256": inventory[f"{folder}/frame.png"]},
    ]}, "fixture")
    inputs = {"package": "a" * 64, "attempt": "attempt-1", "capture": 1, "version": VERSION}
    report_id = "processing-" + hashlib.sha256(encoded(inputs).encode()).hexdigest()[:24]
    common = {"contract_version": "0.3", "run_id": RUN, "source_type": "replay",
              "provenance": {"source": "replay", "status": "pending_confirmation"},
              "lineage": {"source": "replay", "producer": VERSION, "producer_version": "fixture", "derived_from": report_id}}
    report = {"report_id": report_id, "run_id": RUN, "attempt_id": "attempt-1", "capture_attempt_seq": 1,
              "producer_version": VERSION, "input_sha256": {**inputs, **inventory}, "observations": [],
              "detections": [], "localization_estimates": [], "gaps": []}
    for number in range(2):
        oid = f"observation-{number}"
        observation = {**common, "observation_id": oid, "captured_at": "2026-08-24T02:00:05Z",
                       "image_region": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.5, "y_max": 0.5,
                                        "image_width": 100, "image_height": 100},
                       "detector": {"kind": "marker_proxy", "version": "fixture"}, "confidence": 0,
                       "calibration_ref": "sha256:" + inventory["config/camera_calibration.json"]}
        position = {"x": 1.0, "y": 2.0, "z": 3.0} if number == 0 else None
        local = {**common, "localization_id": f"local-{number}", "observation_id": oid,
                 "status": "needs_review" if position else "unmatched", "position": position,
                 "heading_deg": None, "residual_m": None, "method": "fixture"}
        save(db, "observation", oid, RUN, observation, "fixture")
        save(db, "localization", local["localization_id"], RUN, local, "fixture")
        report["observations"].append(oid)
        report["detections"].append({"observation_id": oid, "dictionary": "DICT_4X4_50", "marker_id": number})
        report["localization_estimates"].append({"observation_id": oid, "marker_id": number,
            "status": "estimated_needs_review" if position else "blocked", "reason": "synthetic_fixture",
            **({"position": position, "conditional_max_position_std_m": 0.01, "timing_displacement_bound_m": 0.02} if position else {})})
    save(db, "processing_report", report_id, RUN, report, "fixture")
    yield db, report
    db.close()


def replace_report(db, report):
    db.execute("UPDATE instrument_records SET payload=? WHERE kind='processing_report' AND id=?",
               (encoded(report), report["report_id"]))


def test_pages_positions_and_retains_blocked_records(markers):
    db, _ = markers
    first = page(db, RUN, "", 1)
    assert first["scene_version_id"] == "scene-001-v1"
    assert first["items"][0]["localization"]["position"] == {"x": 1, "y": 2, "z": 3}
    assert first["items"][0]["localization"]["status"] == "needs_review"
    assert first["next_cursor"] == "local-0"
    second = page(db, RUN, first["next_cursor"], 1)
    assert second["items"][0]["localization"]["position"] is None
    assert second["items"][0]["reason"] == "synthetic_fixture"
    assert second["next_cursor"] is None


@pytest.mark.parametrize("change", ["package", "input", "position", "duplicate", "capture"])
def test_rejects_inconsistent_report_or_capture(markers, change):
    db, original = markers
    report = copy.deepcopy(original)
    if change == "package": report["input_sha256"]["package"] = "b" * 64
    if change == "input": report["input_sha256"]["config/scene_alignment.yaml"] = "b" * 64
    if change == "position": report["localization_estimates"][0]["position"]["x"] = 100
    if change == "duplicate": report["detections"].append(report["detections"][0])
    if change == "capture": report["capture_attempt_seq"] = 2
    replace_report(db, report)
    with pytest.raises(ValueError): page(db, RUN, "", 25)


def test_rejects_changed_scene_binding(markers):
    db, _ = markers
    db.execute("UPDATE tasks SET scene_version_id='another-version' WHERE id=?", (RUN,))
    with pytest.raises(ValueError, match="scene binding"): page(db, RUN, "", 25)


def test_unlinked_manual_record_is_not_projected_as_marker_evidence(markers):
    db, _ = markers
    db.execute("UPDATE instrument_records SET payload=json_set(payload,'$.lineage.derived_from','manual') WHERE kind='localization'")
    assert page(db, RUN, "", 25)["items"] == []


def test_endpoint_is_protected(markers, client, monkeypatch):
    from app.routers import get_db

    db, _ = markers
    monkeypatch.delenv("FIELD_AUTH_FILE", raising=False)
    monkeypatch.setenv("ASTRA_API_TOKEN", "marker-view-test-token")
    client.app.dependency_overrides[get_db] = lambda: db
    try:
        url = f"/api/instruments/runs/{RUN}/marker-localizations"
        assert client.get(url).status_code == 401
        response = client.get(url, headers={"Authorization": "Bearer marker-view-test-token"})
        assert response.status_code == 200
        assert len(response.json()["items"]) == 2
        assert "archive_path" not in response.text
    finally: client.app.dependency_overrides.clear()
