"""Loopback Local Evidence Report and Field-Replay Acceptance CLI."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.acceptance import AcceptanceError, accept_run, evaluate_landmarks, list_acceptance_records
from app.cli import main
from app.inspection_package import InspectionPackageManifest
from app.local_report import create_local_app

from .formal_helpers import (
    COMPLETED,
    EXCEPTIONS,
    import_named,
    inbox_copy,
    register_golden_scene,
    set_landmark_residuals,
)

GOLDEN_ID = "golden-run-v2-completed"
EXCEPTIONS_ID = "golden-run-v2-completed-with-exceptions"


def _import_completed(client) -> None:
    register_golden_scene(client)
    inbox_copy(client, COMPLETED, "v2-completed")
    import_named(client, "v2-completed")


def _cli_env(client, monkeypatch) -> None:
    monkeypatch.setenv("TWIN_DB_PATH", client.app.state.settings.db_path)
    monkeypatch.setenv("TWIN_IMPORT_ROOT", client.app.state.settings.import_root)


def test_cli_accepts_when_automatic_and_frames_pass(client, monkeypatch) -> None:
    _import_completed(client)
    _cli_env(client, monkeypatch)
    rc = main(
        [
            "accept-run",
            GOLDEN_ID,
            "--operator-label",
            "现场操作员",
            "--first-frame",
            "pass",
            "--last-frame",
            "pass",
            "--remarks",
            "首尾帧与地标均正确",
        ]
    )
    assert rc == 0
    task = client.get(f"/api/tasks/{GOLDEN_ID}").json()
    assert task["acceptance_state"] == "accepted"
    assert task["acceptance_recorded_at"].endswith("Z")
    assert "first_frame" not in task
    summary = task["acceptance_summary"]
    assert summary["first_frame"] == "pass"
    assert summary["last_frame"] == "pass"
    assert summary["required_passed"] == summary["required_total"] >= 3
    assert summary["auxiliary_failed"] == 0
    records = list_acceptance_records(client.app.state.db, GOLDEN_ID)
    assert len(records) == 1
    assert records[0]["operator_label"] == "现场操作员"
    assert records[0]["first_frame"] == "pass"
    assert records[0]["last_frame"] == "pass"
    assert records[0]["outcome"] == "accepted"
    assert records[0]["kind"] == "acceptance"
    assert all(item["passed"] for item in records[0]["landmarks"])


def test_frames_must_both_pass(client) -> None:
    _import_completed(client)
    with pytest.raises(AcceptanceError) as exc:
        accept_run(
            client.app.state.db,
            GOLDEN_ID,
            operator_label="操作员",
            first_frame="pass",
            last_frame="fail",
            remarks="",
        )
    assert exc.value.code == "frames_not_passed"
    task = client.get(f"/api/tasks/{GOLDEN_ID}").json()
    assert task["acceptance_state"] == "pending_acceptance"


def test_auxiliary_unobserved_does_not_block_acceptance(client) -> None:
    register_golden_scene(client)
    inbox_copy(client, EXCEPTIONS, "v2-exceptions")
    import_named(client, "v2-exceptions")
    result = accept_run(
        client.app.state.db,
        EXCEPTIONS_ID,
        operator_label="操作员",
        first_frame="pass",
        last_frame="pass",
        remarks="辅助码未出现",
    )
    aux = [item for item in result["landmarks"] if item["role"] == "auxiliary"]
    assert aux and aux[0]["reason"] == "auxiliary_unobserved" and aux[0]["passed"] is True
    assert client.get(f"/api/tasks/{EXCEPTIONS_ID}").json()["acceptance_state"] == "accepted"


def test_required_unobserved_keeps_pending(client) -> None:
    _import_completed(client)
    client.app.state.db.execute(
        "INSERT INTO registered_landmarks "
        "(landmark_id, alignment_id, marker_id, dictionary, role, physical_size_m, "
        "pos_x, pos_y, pos_z, roll_deg, pitch_deg, yaw_deg, translation_m, rotation_deg, "
        "min_valid_samples, route_portion) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "aruco-missing-99",
            "alignment-scene-001-v1",
            99,
            "4X4_50",
            "required",
            0.2,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.05,
            2.0,
            3,
            "middle",
        ),
    )
    with pytest.raises(AcceptanceError) as exc:
        accept_run(
            client.app.state.db,
            GOLDEN_ID,
            operator_label="操作员",
            first_frame="pass",
            last_frame="pass",
            remarks="",
        )
    assert exc.value.code == "automatic_landmarks_failed"
    missing = [
        item for item in exc.value.details["landmarks"] if item["landmark_id"] == "aruco-missing-99"
    ]
    assert missing and missing[0]["reason"] == "required_unobserved"
    assert client.get(f"/api/tasks/{GOLDEN_ID}").json()["acceptance_state"] == "pending_acceptance"


def test_auxiliary_out_of_tolerance_blocks(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "aux-fail")
    set_landmark_residuals(source, "aruco-aux-10", [1.0, 1.1, 1.2], [0.2, 0.2, 0.2])
    import_named(client, "aux-fail")
    with pytest.raises(AcceptanceError) as exc:
        accept_run(
            client.app.state.db,
            GOLDEN_ID,
            operator_label="操作员",
            first_frame="pass",
            last_frame="pass",
            remarks="",
        )
    assert exc.value.code == "automatic_landmarks_failed"
    aux = [item for item in exc.value.details["landmarks"] if item["landmark_id"] == "aruco-aux-10"]
    assert aux and aux[0]["passed"] is False
    assert client.get(f"/api/tasks/{GOLDEN_ID}").json()["acceptance_state"] == "pending_acceptance"


def test_dual_threshold_blocks_acceptance(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "thresholds")
    set_landmark_residuals(
        source,
        "aruco-begin-01",
        [1.0, 1.2, 1.4],
        [20.0, 21.0, 22.0],
    )
    import_named(client, "thresholds")
    with pytest.raises(AcceptanceError) as exc:
        accept_run(
            client.app.state.db,
            GOLDEN_ID,
            operator_label="操作员",
            first_frame="pass",
            last_frame="pass",
            remarks="",
        )
    begin = [
        item for item in exc.value.details["landmarks"] if item["landmark_id"] == "aruco-begin-01"
    ][0]
    assert begin["translation_p95_m"] > begin["translation_limit_m"]
    assert begin["rotation_p95_deg"] > begin["rotation_limit_deg"]
    assert client.get(f"/api/tasks/{GOLDEN_ID}").json()["acceptance_state"] == "pending_acceptance"


def test_sample_count_below_minimum_keeps_pending(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "few-samples")
    set_landmark_residuals(source, "aruco-mid-02", [0.01], [0.2])
    import_named(client, "few-samples")
    with pytest.raises(AcceptanceError) as exc:
        accept_run(
            client.app.state.db,
            GOLDEN_ID,
            operator_label="操作员",
            first_frame="pass",
            last_frame="pass",
            remarks="",
        )
    mid = [
        item for item in exc.value.details["landmarks"] if item["landmark_id"] == "aruco-mid-02"
    ][0]
    assert mid["reason"] == "sample_count_below_minimum"
    assert mid["sample_count"] == 1
    assert client.get(f"/api/tasks/{GOLDEN_ID}").json()["acceptance_state"] == "pending_acceptance"


def test_forged_declared_percentiles_are_recomputed(client) -> None:
    _import_completed(client)
    row = client.app.state.db.query_one(
        "SELECT manifest_json, alignment_id FROM run_bundle_imports WHERE run_id = ?",
        (GOLDEN_ID,),
    )
    raw = json.loads(row["manifest_json"])
    for landmark in raw["acceptance_evidence"]["landmarks"]:
        for observation in landmark["observations"]:
            observation["translation_residual_m"] = 1.5
            observation["rotation_residual_deg"] = 12.0
        landmark["translation_p95_m"] = 0.001
        landmark["rotation_p95_deg"] = 0.001
    manifest = InspectionPackageManifest.model_validate(raw)
    registered = client.app.state.db.query_all(
        "SELECT * FROM registered_landmarks WHERE alignment_id = ?",
        (row["alignment_id"],),
    )
    verdicts = evaluate_landmarks(manifest, registered)
    assert all(item["observed"] for item in verdicts if item["role"] == "required")
    assert any(item["translation_p95_m"] > item["translation_limit_m"] for item in verdicts)
    assert not any(item["translation_p95_m"] == 0.001 for item in verdicts if item["observed"])


def test_local_evidence_report_is_loopback_only(client) -> None:
    _import_completed(client)
    local = TestClient(create_local_app(client.app.state.settings))
    response = local.get(f"/local/evidence-reports/{GOLDEN_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == GOLDEN_ID
    assert "archive_path" not in body
    assert body["first_frame"] == "acceptance/first_frame.png"
    assert body["last_frame"] == "acceptance/last_frame.png"
    assert body["identity_note"]
    assert "authenticated" in body["identity_note"]
    assert body["landmarks"][0]["lineage"]
    html = local.get(f"/local/evidence-reports/{GOLDEN_ID}/html")
    assert html.status_code == 200
    assert "Local Evidence Report" in html.text
    assert "first frame" in html.text
    assert "registered pose" in html.text
    assert f"/local/evidence-reports/{GOLDEN_ID}/files/acceptance/first_frame.png" in html.text
    image = local.get(f"/local/evidence-reports/{GOLDEN_ID}/files/acceptance/first_frame.png")
    assert image.status_code == 200
    traversal = local.get(f"/local/evidence-reports/{GOLDEN_ID}/files/../../twin-test.db")
    assert traversal.status_code == 404
    other = local.get(f"/local/evidence-reports/{GOLDEN_ID}/files/replay/trajectory.json")
    assert other.status_code == 404
    blocked = local.get(
        f"/local/evidence-reports/{GOLDEN_ID}",
        headers={"X-Forwarded-For": "8.8.8.8"},
    )
    assert blocked.status_code == 403
    assert client.get(f"/local/evidence-reports/{GOLDEN_ID}").status_code == 404
    public = client.get(f"/api/tasks/{GOLDEN_ID}").json()
    assert "acceptance/first_frame.png" not in json.dumps(public)


def test_serve_evidence_report_binds_loopback(client, monkeypatch) -> None:
    captured: dict = {}

    def fake_run(app, host, port):
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    _cli_env(client, monkeypatch)
    assert main(["serve-evidence-report", "--port", "8091"]) == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8091


def test_operator_label_is_not_authenticated_identity(client) -> None:
    _import_completed(client)
    result = accept_run(
        client.app.state.db,
        GOLDEN_ID,
        operator_label="现场操作员",
        first_frame="pass",
        last_frame="pass",
        remarks="",
    )
    assert "not authenticated identity" in result["identity_note"]
