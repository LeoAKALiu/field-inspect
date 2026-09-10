"""Rejected Package, quarantine, idempotency, interrupt recovery, single-writer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.run_bundle import BundleImportError, list_rejections
from app.writer_lock import SingleWriterError

from .formal_helpers import (
    COMPLETED,
    V1_FIXTURE,
    import_named,
    inbox_copy,
    refresh_inventory,
    register_golden_scene,
    rewrite_manifest,
    rewrite_trajectory,
    save_bag,
)

GOLDEN_ID = "golden-run-v2-completed"


def _inbox_source(client, name: str) -> Path:
    return Path(client.app.state.settings.import_root) / "inbox" / name


def _assert_no_imported_run(client, run_id: str = GOLDEN_ID) -> None:
    assert client.get(f"/api/tasks/{run_id}").status_code == 404
    assert all(item["run_id"] != run_id for item in client.get("/api/imports").json())


def test_diagnostic_statuses_cannot_create_imported_run(client) -> None:
    register_golden_scene(client)
    for status in ("incomplete", "aborted", "failed"):
        name = f"diag-{status}"
        source = inbox_copy(client, COMPLETED, name)
        rewrite_manifest(
            source,
            lambda manifest, value=status: manifest.__setitem__("status", value),
        )
        with pytest.raises(BundleImportError) as exc:
            import_named(client, name)
        assert exc.value.code == "diagnostic_package"
        assert "stage" in exc.value.details
        assert source.is_dir()
        _assert_no_imported_run(client)


def test_synthetic_v2_package_cannot_create_imported_run(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "synthetic-v2")
    rewrite_manifest(
        source,
        lambda manifest: manifest.__setitem__("source_kind", "synthetic_contract_fixture"),
    )
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "synthetic-v2")
    assert exc.value.code == "synthetic_package"
    assert source.is_dir()
    _assert_no_imported_run(client)


def test_real_v1_inspection_run_is_rejected(client) -> None:
    source = inbox_copy(client, V1_FIXTURE, "v1-real")
    manifest_path = source / "data/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_kind"] = "inspection_run"
    manifest["bag"] = {"storage_id": "mcap", "path": "bags/run.mcap"}
    (source / "data/bags").mkdir(parents=True, exist_ok=True)
    (source / "data/bags/run.mcap").write_bytes(b"\x89MCAP0\r\n")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    save_bag(source)
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "v1-real")
    assert exc.value.code == "bundle_invalid"
    assert source.is_dir()
    _assert_no_imported_run(client, "fixture-run-001")


def test_missing_mcap_is_rejected_without_touching_inbox(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "no-mcap")
    (source / "data/bags/run.mcap").unlink()
    refresh_inventory(source)
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "no-mcap")
    assert exc.value.code == "bundle_invalid"
    assert source.is_dir()
    assert (source / "data/manifest.json").is_file()
    _assert_no_imported_run(client)


def test_checksum_failure_quarantines_staging_only(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "tampered")
    trajectory = source / "data/replay/trajectory.json"
    trajectory.write_text(trajectory.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "tampered")
    assert exc.value.code == "bundle_invalid"
    quarantine = Path(exc.value.details["quarantine_path"])
    assert quarantine.is_dir()
    assert source.is_dir()
    _assert_no_imported_run(client)


def test_illegal_path_is_rejected(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "traversal")
    rewrite_manifest(
        source,
        lambda manifest: manifest["replay"].__setitem__("trajectory_path", "../secret.json"),
    )
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "traversal")
    assert exc.value.code == "bundle_invalid"
    assert source.is_dir()
    _assert_no_imported_run(client)


def test_contract_error_is_rejected(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "bad-seq")
    rewrite_trajectory(
        source,
        lambda trajectory: trajectory["points"][1].__setitem__("seq", 9),
    )
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "bad-seq")
    assert exc.value.code == "bundle_invalid"
    _assert_no_imported_run(client)


def test_unregistered_scene_is_rejected(client) -> None:
    source = inbox_copy(client, COMPLETED, "no-reg")
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "no-reg")
    assert exc.value.code == "alignment_not_registered"
    assert source.is_dir()
    _assert_no_imported_run(client)


def test_unknown_alignment_is_rejected(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "unknown-align")

    def mutate(manifest: dict) -> None:
        manifest["alignment"]["alignment_id"] = "alignment-unknown"

    rewrite_manifest(source, mutate)
    rewrite_trajectory(
        source,
        lambda trajectory: trajectory.__setitem__("alignment_id", "alignment-unknown"),
    )
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "unknown-align")
    assert exc.value.code == "alignment_not_registered"
    _assert_no_imported_run(client)


def test_scene_version_mismatch_is_rejected(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "scene-mismatch")
    scene_file = source / "data/scene/scene-version.json"
    scene_file.write_text(scene_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    digest = __import__("hashlib").sha256(scene_file.read_bytes()).hexdigest()
    rewrite_manifest(
        source,
        lambda manifest: manifest["scene_version"].__setitem__("asset_sha256", digest),
    )
    refresh_inventory(source)
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "scene-mismatch")
    assert exc.value.code == "scene_alignment_mismatch"
    _assert_no_imported_run(client)


def test_alignment_digest_mismatch_is_rejected(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "align-mismatch")
    evidence = source / "data/alignment/evidence.json"
    evidence.write_text(evidence.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    digest = __import__("hashlib").sha256(evidence.read_bytes()).hexdigest()
    rewrite_manifest(
        source,
        lambda manifest: manifest["alignment"].__setitem__("evidence_sha256", digest),
    )
    refresh_inventory(source)
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "align-mismatch")
    assert exc.value.code == "scene_alignment_mismatch"
    _assert_no_imported_run(client)


def test_synthetic_alignment_is_rejected(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "synth-align")
    rewrite_manifest(
        source,
        lambda manifest: manifest["alignment"].__setitem__("alignment_id", "synthetic-alignment"),
    )
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "synth-align")
    assert exc.value.code == "synthetic_alignment"
    _assert_no_imported_run(client)


def test_missing_required_landmark_is_rejected(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "missing-required")

    def mutate(manifest: dict) -> None:
        manifest["acceptance_evidence"]["landmarks"] = [
            item
            for item in manifest["acceptance_evidence"]["landmarks"]
            if item["landmark_id"] != "aruco-begin-01"
        ]

    rewrite_manifest(source, mutate)
    (source / "data/acceptance/landmarks/aruco-begin-01.png").unlink()
    refresh_inventory(source)
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "missing-required")
    assert exc.value.code == "required_landmark_missing"
    assert source.is_dir()
    _assert_no_imported_run(client)


def test_landmark_registration_mismatch_is_rejected(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "marker-mismatch")

    def mutate(manifest: dict) -> None:
        manifest["acceptance_evidence"]["landmarks"][0]["marker_id"] = 99

    rewrite_manifest(source, mutate)
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "marker-mismatch")
    assert exc.value.code == "landmark_registration_mismatch"
    _assert_no_imported_run(client)


def test_incomplete_lineage_is_rejected(client) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "bad-lineage")

    def mutate(manifest: dict) -> None:
        landmark = manifest["acceptance_evidence"]["landmarks"][0]
        landmark["observations"][0]["camera_calibration_sha256"] = "0" * 64

    rewrite_manifest(source, mutate)
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "bad-lineage")
    assert exc.value.code == "bundle_invalid"
    _assert_no_imported_run(client)


def test_rejection_record_is_structured_and_public_http_hides_it(client, monkeypatch) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "reject-record")
    rewrite_manifest(source, lambda manifest: manifest.__setitem__("status", "failed"))
    with pytest.raises(BundleImportError):
        import_named(client, "reject-record")
    rows = list_rejections(client.app.state.db)
    assert rows
    assert rows[0]["code"] == "diagnostic_package"
    assert rows[0]["stage"] == "validate"
    assert rows[0]["inbox_name"] == "reject-record"
    assert rows[0]["quarantine_path"]
    assert client.get("/api/rejections").status_code == 404
    assert client.post("/api/imports/reject-record").status_code in (404, 405)
    monkeypatch.setenv("TWIN_DB_PATH", client.app.state.settings.db_path)
    monkeypatch.setenv("TWIN_IMPORT_ROOT", client.app.state.settings.import_root)
    from app.cli import main

    assert main(["list-rejections"]) == 0


def test_idempotent_reimport_and_conflict_do_not_cover_history(client) -> None:
    register_golden_scene(client)
    original = inbox_copy(client, COMPLETED, "original")
    first = import_named(client, "original")
    second = import_named(client, "original")
    assert first["status"] == "imported"
    assert second["status"] == "duplicate"
    assert second["bundle_sha256"] == first["bundle_sha256"]
    assert original.is_dir()
    task = client.get(f"/api/tasks/{GOLDEN_ID}").json()
    assert task["acceptance_state"] == "pending_acceptance"

    changed = inbox_copy(client, COMPLETED, "changed")
    rewrite_trajectory(
        changed,
        lambda trajectory: trajectory["points"][1]["position"].__setitem__("x", -51.11),
    )
    with pytest.raises(BundleImportError) as exc:
        import_named(client, "changed")
    assert exc.value.code == "run_id_conflict"
    still = client.get(f"/api/tasks/{GOLDEN_ID}").json()
    points = client.get(f"/api/tasks/{GOLDEN_ID}/trajectory").json()
    assert still["id"] == GOLDEN_ID
    assert points[1]["position"]["x"] != -51.11
    assert changed.is_dir()


@pytest.mark.parametrize("stage", ["copy", "validate", "archive", "ledger", "trajectory"])
def test_interrupt_then_retry_does_not_leave_a_partial_run(client, stage: str) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, f"crash-{stage}")
    with pytest.raises(KeyboardInterrupt):
        import_named(client, f"crash-{stage}", crash_after=stage)
    _assert_no_imported_run(client)
    assert source.is_dir()
    result = import_named(client, f"crash-{stage}")
    assert result["status"] in ("imported", "duplicate")
    task = client.get(f"/api/tasks/{GOLDEN_ID}")
    assert task.status_code == 200
    points = client.get(f"/api/tasks/{GOLDEN_ID}/trajectory").json()
    assert [point["seq"] for point in points] == [0, 1, 2]
    assert "archive_path" not in client.get("/api/imports").json()[0]


def test_multi_worker_configuration_fails_fast(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TWIN_DB_PATH", str(tmp_path / "twin.db"))
    monkeypatch.setenv("TWIN_IMPORT_ROOT", str(tmp_path / "imports"))
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    from app.main import create_app

    with pytest.raises(SingleWriterError):
        create_app()


def test_second_process_writer_lock_fails_fast(client) -> None:
    register_golden_scene(client)
    inbox_copy(client, COMPLETED, "locked")
    root = client.app.state.settings.import_root
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; from app.writer_lock import ImportWriterLock; "
            f"lock = ImportWriterLock({root!r}); lock.__enter__(); time.sleep(12)",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
    )
    try:
        time.sleep(0.4)
        with pytest.raises(BundleImportError) as exc:
            import_named(client, "locked")
        assert exc.value.code == "import_lock_held"
        _assert_no_imported_run(client)
    finally:
        holder.terminate()
        holder.wait(timeout=5)
