"""Offline USB BagIt import through the server-local inbox interface."""

from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import bagit
import jsonschema
import pytest

from app.run_bundle import BundleImportError, import_inbox_directory, import_run_bundle

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "data/contract-fixtures/scout-run-bagit-v1"
SCHEMA_PATH = REPO_ROOT / "packages/contracts/run-bundle.schema.json"


def import_named(client, name: str):
    return import_inbox_directory(
        client.app.state.db,
        client.app.state.settings.import_root,
        name,
    )


def archive_dir(client, run_id: str) -> Path:
    row = client.app.state.db.query_one(
        "SELECT archive_path FROM run_bundle_imports WHERE run_id = ?",
        (run_id,),
    )
    assert row is not None
    return Path(row["archive_path"])


def copy_to_inbox(client, tmp_path: Path, name: str = "fixture") -> Path:
    inbox = Path(client.app.state.settings.import_root) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / name
    shutil.copytree(FIXTURE_PATH, target)
    return target


def update_bag_manifests(path: Path) -> None:
    bag = bagit.Bag(str(path))
    bag.save(manifests=True)
    bag.validate(processes=1)


def test_fixture_is_valid_bagit_and_matches_json_schemas() -> None:
    bag = bagit.Bag(str(FIXTURE_PATH))
    bag.validate(processes=1)
    assert bag.version_info == (1, 0)
    assert set(bag.payload_entries()) == {
        "data/config/system.yaml",
        "data/manifest.json",
        "data/replay/trajectory.json",
    }

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    manifest = json.loads((FIXTURE_PATH / "data/manifest.json").read_text(encoding="utf-8"))
    trajectory = json.loads(
        (FIXTURE_PATH / "data/replay/trajectory.json").read_text(encoding="utf-8")
    )
    validator.evolve(schema=schema["$defs"]["Manifest"]).validate(manifest)
    validator.evolve(schema=schema["$defs"]["Trajectory"]).validate(trajectory)


def test_import_copies_valid_bundle_then_materializes_existing_replay_interfaces(
    client,
    tmp_path: Path,
) -> None:
    source = copy_to_inbox(client, tmp_path)
    result = import_named(client, "fixture")

    assert result["status"] == "imported"
    assert result["run_id"] == "fixture-run-001"
    assert result["trajectory_points"] == 2
    assert len(result["bundle_sha256"]) == 64
    assert source.is_dir(), "USB/inbox source must remain untouched"

    task = client.get("/api/tasks/fixture-run-001")
    assert task.status_code == 200
    assert task.json()["status"] == "completed"
    assert task.json()["mode"] == "replay"
    assert task.json()["run_kind"] == "demonstration"
    assert task.json()["distance_m"] == 0.204

    trajectory = client.get("/api/tasks/fixture-run-001/trajectory")
    assert trajectory.status_code == 200
    assert [point["seq"] for point in trajectory.json()] == [0, 1]
    assert trajectory.json()[1]["position"] == {"x": -51.8, "y": 1.0, "z": -17.96}

    imports = client.get("/api/imports").json()
    assert len(imports) == 1
    assert "archive_path" not in imports[0]
    archive = archive_dir(client, "fixture-run-001")
    assert archive.is_dir()
    assert bagit.Bag(str(archive)).is_valid()


def test_reimport_of_same_run_and_payload_is_idempotent(client, tmp_path: Path) -> None:
    copy_to_inbox(client, tmp_path)
    first = import_named(client, "fixture")
    second = import_named(client, "fixture")

    assert second["status"] == "duplicate"
    assert second["bundle_sha256"] == first["bundle_sha256"]
    assert len(client.get("/api/tasks/fixture-run-001/trajectory").json()) == 2
    assert len(client.get("/api/imports").json()) == 1


def test_concurrent_duplicate_import_is_serialized(client, tmp_path: Path) -> None:
    source = copy_to_inbox(client, tmp_path)
    import_root = Path(client.app.state.settings.import_root)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(import_run_bundle, client.app.state.db, source, import_root)
            for _ in range(2)
        ]
    results = []
    for future in futures:
        try:
            results.append(future.result())
        except BundleImportError as exc:
            assert exc.code == "import_lock_held"
    assert len(client.get("/api/imports").json()) == 1
    assert (
        any(result["status"] == "imported" for result in results)
        or client.get("/api/tasks/fixture-run-001").status_code == 200
    )


def test_checksum_failure_is_quarantined_without_partial_database_rows(
    client,
    tmp_path: Path,
) -> None:
    source = copy_to_inbox(client, tmp_path, "tampered")
    trajectory_path = source / "data/replay/trajectory.json"
    trajectory_path.write_text(trajectory_path.read_text() + "\n", encoding="utf-8")

    with pytest.raises(BundleImportError) as exc:
        import_named(client, "tampered")

    assert exc.value.code == "bundle_invalid"
    quarantine = Path(exc.value.details["quarantine_path"])
    assert quarantine.is_dir()
    assert client.get("/api/tasks/fixture-run-001").status_code == 404
    assert client.get("/api/imports").json() == []


def test_same_run_id_with_different_valid_payload_is_quarantined_as_conflict(
    client,
    tmp_path: Path,
) -> None:
    copy_to_inbox(client, tmp_path, "original")
    changed = copy_to_inbox(client, tmp_path, "changed")
    assert import_named(client, "original")["status"] == "imported"

    trajectory_path = changed / "data/replay/trajectory.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["points"][1]["position"]["x"] = -51.7
    trajectory_path.write_text(json.dumps(trajectory, indent=2), encoding="utf-8")
    update_bag_manifests(changed)

    with pytest.raises(BundleImportError) as exc:
        import_named(client, "changed")

    assert exc.value.code == "run_id_conflict"
    quarantine = Path(exc.value.details["quarantine_path"])
    assert quarantine.is_dir()
    points = client.get("/api/tasks/fixture-run-001/trajectory").json()
    assert points[1]["position"]["x"] == -51.8


def test_semantically_invalid_trajectory_rolls_back_and_is_quarantined(
    client,
    tmp_path: Path,
) -> None:
    invalid = copy_to_inbox(client, tmp_path, "invalid-seq")
    trajectory_path = invalid / "data/replay/trajectory.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["points"][1]["seq"] = 7
    trajectory_path.write_text(json.dumps(trajectory, indent=2), encoding="utf-8")
    update_bag_manifests(invalid)

    with pytest.raises(BundleImportError) as exc:
        import_named(client, "invalid-seq")

    assert exc.value.code == "bundle_invalid"
    assert client.get("/api/tasks/fixture-run-001").status_code == 404
    assert client.get("/api/imports").json() == []


def test_public_http_cannot_trigger_import(client, tmp_path: Path) -> None:
    copy_to_inbox(client, tmp_path)
    response = client.post("/api/imports/fixture")
    assert response.status_code in (404, 405)
    assert client.get("/api/tasks/fixture-run-001").status_code == 404


def test_inbox_cli_rejects_missing_bundle(client) -> None:
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "not-present")
    assert exc.value.code == "bundle_not_found"
    assert exc.value.status_code == 404
