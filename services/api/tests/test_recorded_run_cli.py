"""Scene Registration CLI, inbox-only Import CLI, and Recorded Run API seams."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.cli import main
from app.config import DurablePathError, Settings, assert_durable_paths
from app.run_bundle import BundleImportError, import_inbox_directory
from app.scene_registration import SceneRegistrationError, register_scene_from_file

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRATION = REPO_ROOT / "data/contract-fixtures/scene-registration-scene-001-v1.json"
COMPLETED = REPO_ROOT / "data/contract-fixtures/inspection-package-v2-completed"
EXCEPTIONS = REPO_ROOT / "data/contract-fixtures/inspection-package-v2-completed-with-exceptions"


def _register(client, operator: str = "现场操作员") -> dict:
    return register_scene_from_file(client.app.state.db, REGISTRATION, operator_label=operator)


def _inbox(client, source: Path, name: str) -> Path:
    inbox = Path(client.app.state.settings.import_root) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / name
    shutil.copytree(source, target)
    return target


def test_durable_paths_reject_code_release_locations(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "services" / "api").mkdir(parents=True)
    settings = Settings(
        db_path=str(repo / "services" / "api" / "twin.db"),
        import_root=str(tmp_path / "imports"),
        sim_tick_ms=50,
        seed=1,
    )
    with pytest.raises(DurablePathError, match="TWIN_DB_PATH"):
        assert_durable_paths(settings, repo_root=repo)


def test_durable_paths_allow_data_var(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "data" / "var").mkdir(parents=True)
    settings = Settings(
        db_path=str(repo / "data" / "var" / "twin.db"),
        import_root=str(repo / "data" / "imports"),
        sim_tick_ms=50,
        seed=1,
    )
    assert_durable_paths(settings, repo_root=repo)


def test_register_scene_is_immutable_and_idempotent(client) -> None:
    first = _register(client)
    assert first["status"] == "registered"
    assert first["operator_label"] == "现场操作员"
    assert first["required_landmarks"] == 3
    second = _register(client, operator="someone-else")
    assert second["status"] == "duplicate"
    row = client.app.state.db.query_one(
        "SELECT operator_label FROM scene_versions WHERE scene_version_id = ?",
        ("scene-001-v1",),
    )
    assert row["operator_label"] == "现场操作员"


def test_register_scene_conflict_does_not_overwrite(client, tmp_path: Path) -> None:
    _register(client)
    tampered = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    tampered["asset_sha256"] = "a" * 64
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(SceneRegistrationError) as exc:
        register_scene_from_file(client.app.state.db, path, operator_label="attacker")
    assert exc.value.code == "registration_conflict"
    row = client.app.state.db.query_one(
        "SELECT asset_sha256 FROM scene_versions WHERE scene_version_id = ?",
        ("scene-001-v1",),
    )
    assert row["asset_sha256"] != "a" * 64


def test_public_http_cannot_create_scene_registration(client) -> None:
    assert client.post("/api/scene-versions").status_code in (404, 405)
    assert client.put("/api/scenes/scene-001").status_code in (404, 405)
    assert client.post("/api/imports/anything").status_code in (404, 405)


def test_v2_inspection_run_without_registration_is_rejected(client) -> None:
    _inbox(client, COMPLETED, "v2-completed")
    with pytest.raises(BundleImportError) as exc:
        import_inbox_directory(
            client.app.state.db, client.app.state.settings.import_root, "v2-completed"
        )
    assert exc.value.code == "alignment_not_registered"
    assert client.get("/api/tasks/golden-run-v2-completed").status_code == 404


def test_cli_imports_recorded_run_pending_acceptance(client, monkeypatch) -> None:
    _register(client)
    _inbox(client, COMPLETED, "v2-completed")
    monkeypatch.setenv("TWIN_DB_PATH", client.app.state.settings.db_path)
    monkeypatch.setenv("TWIN_IMPORT_ROOT", client.app.state.settings.import_root)
    rc = main(["import-package", "v2-completed"])
    assert rc == 0
    task = client.get("/api/tasks/golden-run-v2-completed").json()
    assert task["run_kind"] == "recorded"
    assert task["acceptance_state"] == "pending_acceptance"
    assert task["status"] == "completed"
    assert task["package_status"] == "completed"
    assert task["scene_version_id"] == "scene-001-v1"
    assert task["alignment_id"] == "alignment-scene-001-v1"
    assert task["acceptance_summary"] is None
    assert task["provenance"] == {"source": "replay", "status": "pending_confirmation"}
    version = client.get("/api/scene-versions/scene-001-v1").json()
    assert version["scene_id"] == "scene-001"
    assert version["navigation_default"] is True
    bound = client.get(
        "/api/scenes/scene-001/metadata",
        params={"scene_version_id": "scene-001-v1"},
    ).json()
    assert bound["scene_version_id"] == "scene-001-v1"
    assert bound["asset_sha256"] == version["asset_sha256"]
    client.app.state.db.execute(
        "INSERT INTO scene_versions "
        "(scene_version_id, scene_id, asset_sha256, operator_label, registered_at, "
        "navigation_default) VALUES (?, ?, ?, ?, ?, ?)",
        ("scene-001-v2", "scene-001", "b" * 64, "later", "2026-08-25T00:00:00Z", 1),
    )
    client.app.state.db.execute(
        "UPDATE scene_versions SET navigation_default = 0 WHERE scene_version_id = ?",
        ("scene-001-v1",),
    )
    latest = client.get("/api/scenes/scene-001/metadata").json()
    assert latest["scene_version_id"] == "scene-001-v2"
    historical = client.get(
        "/api/scenes/scene-001/metadata",
        params={"scene_version_id": "scene-001-v1"},
    ).json()
    assert historical["scene_version_id"] == "scene-001-v1"
    assert historical["asset_sha256"] != latest["asset_sha256"]
    still = client.get("/api/tasks/golden-run-v2-completed").json()
    assert still["scene_version_id"] == "scene-001-v1"
    listing = client.get("/api/imports").json()
    assert listing[0]["run_kind"] == "recorded"
    assert "archive_path" not in listing[0]
    demo = client.get("/api/tasks/task-replay-001").json()
    assert demo["run_kind"] == "demonstration"
    assert demo["acceptance_state"] == "not_applicable"


def test_cli_rejects_filesystem_path_as_bundle_name(client) -> None:
    with pytest.raises(BundleImportError) as exc:
        import_inbox_directory(
            client.app.state.db,
            client.app.state.settings.import_root,
            "../secret",
        )
    assert exc.value.code == "invalid_bundle_name"


def test_exceptions_run_stays_visible_and_is_not_auto_accepted(client, monkeypatch) -> None:
    _register(client)
    _inbox(client, EXCEPTIONS, "v2-exceptions")
    monkeypatch.setenv("TWIN_DB_PATH", client.app.state.settings.db_path)
    monkeypatch.setenv("TWIN_IMPORT_ROOT", client.app.state.settings.import_root)
    assert main(["import-package", "v2-exceptions"]) == 0
    task = client.get("/api/tasks/golden-run-v2-completed-with-exceptions").json()
    assert task["status"] == "completed_with_exceptions"
    assert task["package_status"] == "completed_with_exceptions"
    assert task["acceptance_state"] == "pending_acceptance"
    assert task["acceptance_state"] != "accepted"
    assert task["provenance"]["status"] != "confirmed"
    recorded = client.get("/api/tasks", params={"run_kind": "recorded"}).json()
    assert [item["id"] for item in recorded] == ["golden-run-v2-completed-with-exceptions"]
    demo = client.get("/api/tasks", params={"run_kind": "demonstration"}).json()
    assert all(item["run_kind"] == "demonstration" for item in demo)
    assert all(item["id"] != "golden-run-v2-completed-with-exceptions" for item in demo)


def test_register_scene_cli_writes_operator_and_time(client, monkeypatch) -> None:
    monkeypatch.setenv("TWIN_DB_PATH", client.app.state.settings.db_path)
    monkeypatch.setenv("TWIN_IMPORT_ROOT", client.app.state.settings.import_root)
    rc = main(
        [
            "register-scene",
            "--file",
            str(REGISTRATION),
            "--operator-label",
            "cli-operator",
        ]
    )
    assert rc == 0
    row = client.app.state.db.query_one(
        "SELECT operator_label, registered_at FROM scene_versions WHERE scene_version_id = ?",
        ("scene-001-v1",),
    )
    assert row["operator_label"] == "cli-operator"
    assert row["registered_at"].endswith("Z")
