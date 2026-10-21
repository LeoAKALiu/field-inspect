"""Synthetic v2 inputs exercise the import boundary, never a field acceptance."""
import hashlib
import json

import pytest

from app.cli import main
from tests.formal_helpers import (
    COMPLETED,
    inbox_copy,
    refresh_inventory,
    register_golden_scene,
)


@pytest.mark.parametrize("with_index", [False, True])
def test_cli_rejects_capture_without_a_matching_station_attempt(
    client, monkeypatch, capsys, with_index,
):
    register_golden_scene(client)
    package = inbox_copy(client, COMPLETED, "synthetic-orphan-capture")
    manifest = json.loads((package / "data/manifest.json").read_text())
    run_id = manifest["run_id"]
    key = (run_id, "fixture-execution", "fixture-station")
    identity = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]
    directory = package / "data/artifacts/station-captures" / identity / "1"
    directory.mkdir(parents=True)
    (directory / "started.json").write_text(json.dumps({
        "run_id": run_id, "execution_id": key[1], "station_id": key[2],
        "attempt_seq": 1, "state": "started", "reason": "synthetic_fixture",
        "recorded_at_ns": 1, "image_path": None, "image_sha256": None,
    }))
    if with_index:
        index = package / "data/artifacts/stations/attempts.json"
        index.parent.mkdir(parents=True)
        index.write_text(json.dumps({
            "schema_version": "1.0", "artifact_kind": "vehicle_station_attempts",
            "run_id": run_id, "run_status": manifest["status"],
            "generated_at": "2026-10-21T00:00:00Z", "producer_version": "fixture",
            "source_files": [{"path": "config/system.yaml", "sha256":
                hashlib.sha256((package / "data/config/system.yaml").read_bytes()).hexdigest()}],
            "attempts": [],
        }))
        operational = package / "data/metadata/jetson-manifest.json"
        operational.parent.mkdir(parents=True, exist_ok=True)
        operational.write_text(json.dumps({"station_attempts": {
            "path": "artifacts/stations/attempts.json",
            "sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
            "generated_at": "2026-10-21T00:00:00Z", "producer_version": "fixture",
        }}))
    refresh_inventory(package)
    original = {p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    monkeypatch.setenv("TWIN_DB_PATH", client.app.state.settings.db_path)
    monkeypatch.setenv("TWIN_IMPORT_ROOT", client.app.state.settings.import_root)

    assert main(["import-package", package.name]) == 1
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "invalid_station_evidence"
    assert "station" in error["message"]
    assert client.get(f"/api/tasks/{run_id}").status_code == 404
    remaining = {p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    assert remaining == original
