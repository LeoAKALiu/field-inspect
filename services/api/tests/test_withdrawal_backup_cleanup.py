"""Acceptance Withdrawal, Verified Backup, and inbox cleanup CLI."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.acceptance import AcceptanceError, accept_run, list_acceptance_records, withdraw_acceptance
from app.backup import (
    BackupError,
    cleanup_inbox,
    delete_scene_version,
    retire_scene_version,
    verify_backup,
)
from app.cli import main
from app.db import Database

from .formal_helpers import COMPLETED, import_named, inbox_copy, register_golden_scene

GOLDEN_ID = "golden-run-v2-completed"


def _other_disk(path) -> int:
    """Treat the dedicated backup directory as a different device than live data."""
    if {"backup", "empty-backup", "backup-ledger", "backup-good"} & set(Path(path).parts):
        return 2
    return 1


def _import_and_accept(client) -> Path:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "v2-completed")
    import_named(client, "v2-completed")
    accept_run(
        client.app.state.db,
        GOLDEN_ID,
        operator_label="现场操作员",
        first_frame="pass",
        last_frame="pass",
        remarks="验收通过",
    )
    return source


def _copy_sqlite(src: str | Path, dst: Path) -> None:
    destination = sqlite3.connect(dst)
    source = sqlite3.connect(str(src))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()


def _write_backup(client, backup_root: Path) -> Path:
    imported = client.app.state.db.query_one(
        "SELECT archive_path, scene_version_id, alignment_id "
        "FROM run_bundle_imports WHERE run_id = ?",
        (GOLDEN_ID,),
    )
    archive = Path(imported["archive_path"])
    backup_root.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copytree(archive, backup_root / "archive")
    _copy_sqlite(client.app.state.settings.db_path, backup_root / "twin.db")
    scene = client.app.state.db.query_one(
        "SELECT asset_sha256 FROM scene_versions WHERE scene_version_id = ?",
        (imported["scene_version_id"],),
    )
    alignment = client.app.state.db.query_one(
        "SELECT evidence_sha256 FROM verified_alignments WHERE alignment_id = ?",
        (imported["alignment_id"],),
    )
    (backup_root / "scene-version.sha256").write_text(scene["asset_sha256"], encoding="utf-8")
    (backup_root / "alignment.sha256").write_text(alignment["evidence_sha256"], encoding="utf-8")
    return backup_root


def test_withdrawal_appends_and_leaves_history(client, monkeypatch) -> None:
    source = _import_and_accept(client)
    monkeypatch.setenv("TWIN_DB_PATH", client.app.state.settings.db_path)
    monkeypatch.setenv("TWIN_IMPORT_ROOT", client.app.state.settings.import_root)
    rc = main(
        [
            "withdraw-acceptance",
            GOLDEN_ID,
            "--operator-label",
            "复核员",
            "--reason",
            "首帧判读有误",
        ]
    )
    assert rc == 0
    task = client.get(f"/api/tasks/{GOLDEN_ID}").json()
    assert task["acceptance_state"] == "withdrawn"
    assert task["run_kind"] == "recorded"
    records = list_acceptance_records(client.app.state.db, GOLDEN_ID)
    assert [item["kind"] for item in records] == ["acceptance", "withdrawal"]
    assert records[0]["outcome"] == "accepted"
    assert records[1]["outcome"] == "withdrawn"
    assert records[1]["remarks"] == "首帧判读有误"
    assert records[1]["operator_label"] == "复核员"
    assert source.is_dir()
    points = client.get(f"/api/tasks/{GOLDEN_ID}/trajectory").json()
    assert len(points) == 3


def test_reacceptance_allowed_only_when_identities_unchanged(client) -> None:
    _import_and_accept(client)
    withdraw_acceptance(
        client.app.state.db,
        GOLDEN_ID,
        operator_label="复核员",
        reason="人工误判",
    )
    result = accept_run(
        client.app.state.db,
        GOLDEN_ID,
        operator_label="复核员",
        first_frame="pass",
        last_frame="pass",
        remarks="再次确认",
    )
    assert result["outcome"] == "accepted"
    records = list_acceptance_records(client.app.state.db, GOLDEN_ID)
    assert [item["kind"] for item in records] == ["acceptance", "withdrawal", "acceptance"]
    assert client.get(f"/api/tasks/{GOLDEN_ID}").json()["acceptance_state"] == "accepted"


def test_reacceptance_forbidden_when_package_identity_changes(client) -> None:
    _import_and_accept(client)
    withdraw_acceptance(
        client.app.state.db,
        GOLDEN_ID,
        operator_label="复核员",
        reason="对齐可疑",
    )
    imported = client.app.state.db.query_one(
        "SELECT archive_path FROM run_bundle_imports WHERE run_id = ?",
        (GOLDEN_ID,),
    )
    trajectory = Path(imported["archive_path"]) / "data/replay/trajectory.json"
    trajectory.write_text(trajectory.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(AcceptanceError) as exc:
        accept_run(
            client.app.state.db,
            GOLDEN_ID,
            operator_label="复核员",
            first_frame="pass",
            last_frame="pass",
            remarks="想原地复验",
        )
    assert exc.value.code == "reacceptance_forbidden"
    assert client.get(f"/api/tasks/{GOLDEN_ID}").json()["acceptance_state"] == "withdrawn"


def test_reacceptance_forbidden_when_scene_or_alignment_changes(client) -> None:
    _import_and_accept(client)
    original_scene = client.app.state.db.query_one(
        "SELECT asset_sha256 FROM scene_versions WHERE scene_version_id = ?",
        ("scene-001-v1",),
    )["asset_sha256"]
    withdraw_acceptance(
        client.app.state.db,
        GOLDEN_ID,
        operator_label="复核员",
        reason="场景资产被替换",
    )
    client.app.state.db.execute(
        "UPDATE scene_versions SET asset_sha256 = ? WHERE scene_version_id = ?",
        ("c" * 64, "scene-001-v1"),
    )
    with pytest.raises(AcceptanceError) as exc:
        accept_run(
            client.app.state.db,
            GOLDEN_ID,
            operator_label="复核员",
            first_frame="pass",
            last_frame="pass",
            remarks="想原地复验",
        )
    assert exc.value.code == "reacceptance_forbidden"
    assert exc.value.details["reason"] == "scene_version_changed"
    client.app.state.db.execute(
        "UPDATE scene_versions SET asset_sha256 = ? WHERE scene_version_id = ?",
        (original_scene, "scene-001-v1"),
    )
    client.app.state.db.execute(
        "UPDATE verified_alignments SET evidence_sha256 = ? WHERE alignment_id = ?",
        ("d" * 64, "alignment-scene-001-v1"),
    )
    with pytest.raises(AcceptanceError) as exc:
        accept_run(
            client.app.state.db,
            GOLDEN_ID,
            operator_label="复核员",
            first_frame="pass",
            last_frame="pass",
            remarks="对齐已变",
        )
    assert exc.value.code == "reacceptance_forbidden"
    assert exc.value.details["reason"] == "alignment_changed"


def test_same_disk_backup_is_rejected(client, tmp_path, monkeypatch) -> None:
    _import_and_accept(client)
    backup = _write_backup(client, tmp_path / "backup")
    monkeypatch.setattr("app.backup._device", lambda path: 1)
    with pytest.raises(BackupError) as exc:
        verify_backup(client.app.state.db, GOLDEN_ID, backup)
    assert exc.value.code == "same_disk_backup"


def test_backup_in_release_and_incomplete_are_rejected(client, tmp_path, monkeypatch) -> None:
    from app.config import REPO_ROOT

    _import_and_accept(client)
    monkeypatch.setattr("app.backup._device", _other_disk)
    with pytest.raises(BackupError) as exc:
        verify_backup(client.app.state.db, GOLDEN_ID, REPO_ROOT / "services" / "api")
    assert exc.value.code == "backup_in_release"
    empty = tmp_path / "empty-backup"
    empty.mkdir()
    with pytest.raises(BackupError) as exc:
        verify_backup(client.app.state.db, GOLDEN_ID, empty)
    assert exc.value.code == "backup_incomplete"


def test_backup_hash_mismatches_and_cleanup_archive_identity(client, tmp_path, monkeypatch) -> None:
    source = _import_and_accept(client)
    monkeypatch.setattr("app.backup._device", _other_disk)
    backup = _write_backup(client, tmp_path / "backup")
    archive_file = next((backup / "archive").rglob("manifest.json"))
    archive_file.write_text(archive_file.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(BackupError) as exc:
        verify_backup(client.app.state.db, GOLDEN_ID, backup)
    assert exc.value.code == "archive_hash_mismatch"

    backup2 = _write_backup(client, tmp_path / "backup-ledger")
    import sqlite3

    conn = sqlite3.connect(backup2 / "twin.db")
    conn.execute("UPDATE tasks SET name = 'tampered-ledger' WHERE id = ?", (GOLDEN_ID,))
    conn.commit()
    conn.close()
    with pytest.raises(BackupError) as exc:
        verify_backup(client.app.state.db, GOLDEN_ID, backup2)
    assert exc.value.code == "ledger_hash_mismatch"

    good = _write_backup(client, tmp_path / "backup-good")
    verify_backup(client.app.state.db, GOLDEN_ID, good)
    imported = client.app.state.db.query_one(
        "SELECT archive_path FROM run_bundle_imports WHERE run_id = ?",
        (GOLDEN_ID,),
    )
    live_traj = Path(imported["archive_path"]) / "data/replay/trajectory.json"
    live_traj.write_text(live_traj.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(BackupError) as exc:
        cleanup_inbox(
            client.app.state.db,
            client.app.state.settings.import_root,
            "v2-completed",
            GOLDEN_ID,
        )
    assert exc.value.code == "archive_identity_mismatch"
    assert source.is_dir()


def test_verified_backup_then_cleanup(client, tmp_path, monkeypatch) -> None:
    source = _import_and_accept(client)
    assert source.is_dir()
    monkeypatch.setattr("app.backup._device", _other_disk)
    backup = _write_backup(client, tmp_path / "backup")
    monkeypatch.setenv("TWIN_DB_PATH", client.app.state.settings.db_path)
    monkeypatch.setenv("TWIN_IMPORT_ROOT", client.app.state.settings.import_root)
    assert main(["verify-backup", GOLDEN_ID, "--backup-root", str(backup)]) == 0
    assert source.is_dir()
    result = cleanup_inbox(
        client.app.state.db,
        client.app.state.settings.import_root,
        "v2-completed",
        GOLDEN_ID,
    )
    assert result["status"] == "cleaned"
    assert result["archive_retained"] is True
    assert not source.exists()
    imported = client.app.state.db.query_one(
        "SELECT archive_path FROM run_bundle_imports WHERE run_id = ?",
        (GOLDEN_ID,),
    )
    assert Path(imported["archive_path"]).is_dir()
    task = client.get(f"/api/tasks/{GOLDEN_ID}").json()
    assert task["acceptance_state"] == "accepted"


def test_cleanup_requires_acceptance_and_backup(client, tmp_path, monkeypatch) -> None:
    register_golden_scene(client)
    source = inbox_copy(client, COMPLETED, "v2-completed")
    import_named(client, "v2-completed")
    with pytest.raises(BackupError) as exc:
        cleanup_inbox(
            client.app.state.db,
            client.app.state.settings.import_root,
            "v2-completed",
            GOLDEN_ID,
        )
    assert exc.value.code == "not_field_accepted"
    assert source.is_dir()
    accept_run(
        client.app.state.db,
        GOLDEN_ID,
        operator_label="操作员",
        first_frame="pass",
        last_frame="pass",
        remarks="",
    )
    with pytest.raises(BackupError) as exc:
        cleanup_inbox(
            client.app.state.db,
            client.app.state.settings.import_root,
            "v2-completed",
            GOLDEN_ID,
        )
    assert exc.value.code == "backup_required"
    assert source.is_dir()
    monkeypatch.setattr("app.backup._device", _other_disk)
    verify_backup(client.app.state.db, GOLDEN_ID, _write_backup(client, tmp_path / "backup"))
    monkeypatch.setenv("TWIN_DB_PATH", client.app.state.settings.db_path)
    monkeypatch.setenv("TWIN_IMPORT_ROOT", client.app.state.settings.import_root)
    assert source.is_dir()
    assert main(["cleanup-inbox", "v2-completed", "--run-id", GOLDEN_ID]) == 0
    assert not source.exists()


def test_scene_version_referenced_cannot_be_deleted(client) -> None:
    _import_and_accept(client)
    retired = retire_scene_version(client.app.state.db, "scene-001-v1")
    assert retired["deleted"] is False
    assert retired["navigation_default"] is False
    row = client.app.state.db.query_one(
        "SELECT navigation_default FROM scene_versions WHERE scene_version_id = ?",
        ("scene-001-v1",),
    )
    assert row["navigation_default"] == 0
    with pytest.raises(BackupError) as exc:
        delete_scene_version(client.app.state.db, "scene-001-v1")
    assert exc.value.code == "scene_version_in_use"
    still = client.app.state.db.query_one(
        "SELECT scene_version_id FROM scene_versions WHERE scene_version_id = ?",
        ("scene-001-v1",),
    )
    assert still is not None
    assert client.get(f"/api/tasks/{GOLDEN_ID}").json()["scene_version_id"] == "scene-001-v1"


def test_restart_keeps_acceptance_and_backup_state(client, tmp_path, monkeypatch) -> None:
    _import_and_accept(client)
    monkeypatch.setattr("app.backup._device", _other_disk)
    verify_backup(client.app.state.db, GOLDEN_ID, _write_backup(client, tmp_path / "backup"))
    db_path = client.app.state.settings.db_path
    reopened = Database(db_path)
    reopened.init_schema()
    task = reopened.query_one(
        "SELECT acceptance_state FROM tasks WHERE id = ?",
        (GOLDEN_ID,),
    )
    backup = reopened.query_one(
        "SELECT run_id FROM verified_backups WHERE run_id = ?",
        (GOLDEN_ID,),
    )
    records = reopened.query_all(
        "SELECT kind FROM acceptance_records WHERE run_id = ?",
        (GOLDEN_ID,),
    )
    assert task["acceptance_state"] == "accepted"
    assert backup is not None
    assert [row["kind"] for row in records] == ["acceptance"]
    reopened.close()
