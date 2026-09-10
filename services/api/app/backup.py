"""Verified Backup checks and explicit inbox cleanup."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .config import is_inside_code_release
from .db import Database


class BackupError(Exception):
    def __init__(self, code: str, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def ledger_digest(db: Database) -> str:
    """Stable identity of Run Ledger, acceptance history, and referenced assets."""
    parts: list[str] = []
    for table in (
        "run_bundle_imports",
        "acceptance_records",
        "tasks",
        "scene_versions",
        "verified_alignments",
        "registered_landmarks",
        "instrument_assets",
        "instrument_records",
        "instrument_audit",
    ):
        exists = db.query_one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
        rows = db.query_all(f"SELECT * FROM {table}") if exists else []
        payload = sorted([_row_dict(row) for row in rows], key=lambda row: json.dumps(row, sort_keys=True, default=str))
        parts.append(f"{table}:{json.dumps(payload, sort_keys=True, default=str)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _tree_hash(root: Path) -> str:
    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        entries.append(f"{_sha256_file(path)}  {relative}\n")
    return hashlib.sha256("".join(entries).encode("utf-8")).hexdigest()


def _device(path: Path) -> int:
    return path.stat().st_dev


def verify_backup(
    db: Database,
    run_id: str,
    backup_root: Path | str,
    *,
    live_archive: Path | None = None,
    live_db: Path | None = None,
) -> dict:
    """Identity-check an independent backup; same-disk copies are rejected."""
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (run_id,))
    if task is None or task["run_kind"] != "recorded":
        raise BackupError("run_not_recorded", f"{run_id} is not a Recorded Run")
    imported = db.query_one(
        "SELECT archive_path, bundle_sha256, scene_version_id, alignment_id "
        "FROM run_bundle_imports WHERE run_id = ?",
        (run_id,),
    )
    if imported is None:
        raise BackupError("import_not_found", f"no imported package for {run_id}")
    archive = Path(live_archive or imported["archive_path"])
    if not archive.is_dir():
        raise BackupError("archive_missing", "live Package Archive is missing")
    live_db_path = Path(live_db) if live_db is not None else Path(db.path)
    if db.path != ":memory:" and not live_db_path.exists():
        raise BackupError("ledger_missing", "live Run Ledger is missing")
    backup = Path(backup_root)
    if not backup.is_dir():
        raise BackupError("backup_missing", f"backup root {backup} does not exist")
    if is_inside_code_release(backup):
        raise BackupError("backup_in_release", "Verified Backup cannot live in a code-release tree")
    live_dev = _device(archive)
    backup_dev = _device(backup)
    if live_dev == backup_dev:
        raise BackupError(
            "same_disk_backup",
            "a same-disk duplicate is not a Verified Backup",
            details={"live_dev": live_dev, "backup_dev": backup_dev},
        )
    archive_backup = backup / "archive"
    db_backup = backup / "twin.db"
    if not archive_backup.is_dir() or not db_backup.is_file():
        raise BackupError(
            "backup_incomplete",
            "backup must contain archive/ and twin.db",
        )
    live_archive_hash = _tree_hash(archive)
    backup_archive_hash = _tree_hash(archive_backup)
    if live_archive_hash != backup_archive_hash:
        raise BackupError("archive_hash_mismatch", "backup archive does not match Package Archive")
    backup_db = Database(str(db_backup))
    try:
        live_ledger = ledger_digest(db)
        backup_ledger = ledger_digest(backup_db)
    finally:
        backup_db.close()
    if live_ledger != backup_ledger:
        raise BackupError("ledger_hash_mismatch", "backup SQLite does not match the Run Ledger")
    scene = db.query_one(
        "SELECT asset_sha256 FROM scene_versions WHERE scene_version_id = ?",
        (imported["scene_version_id"],),
    )
    alignment = db.query_one(
        "SELECT evidence_sha256 FROM verified_alignments WHERE alignment_id = ?",
        (imported["alignment_id"],),
    )
    scene_file = backup / "scene-version.sha256"
    alignment_file = backup / "alignment.sha256"
    if not scene_file.is_file() or not alignment_file.is_file():
        raise BackupError(
            "asset_identity_missing",
            "backup must include scene and alignment digests",
        )
    if scene_file.read_text(encoding="utf-8").strip() != scene["asset_sha256"]:
        raise BackupError("scene_hash_mismatch", "backup Scene Version digest does not match")
    if alignment_file.read_text(encoding="utf-8").strip() != alignment["evidence_sha256"]:
        raise BackupError("alignment_hash_mismatch", "backup alignment digest does not match")
    recorded_at = _iso_now()
    db.execute(
        "INSERT INTO verified_backups "
        "(run_id, backup_root, device_id, archive_sha256, ledger_sha256, "
        "scene_sha256, alignment_sha256, verified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            str(backup.resolve()),
            backup_dev,
            backup_archive_hash,
            live_ledger,
            scene["asset_sha256"],
            alignment["evidence_sha256"],
            recorded_at,
        ),
    )
    return {
        "run_id": run_id,
        "status": "verified",
        "verified_at": recorded_at,
        "backup_root": str(backup.resolve()),
    }


def cleanup_inbox(
    db: Database,
    import_root: Path | str,
    bundle_name: str,
    run_id: str,
) -> dict:
    """Delete an Inbox Copy only after archive check, field acceptance, and Verified Backup."""
    if not bundle_name or Path(bundle_name).name != bundle_name or bundle_name in (".", ".."):
        raise BackupError("invalid_bundle_name", "bundle_name must be one inbox directory name")
    task = db.query_one("SELECT acceptance_state, run_kind FROM tasks WHERE id = ?", (run_id,))
    if task is None or task["run_kind"] != "recorded":
        raise BackupError("run_not_recorded", f"{run_id} is not a Recorded Run")
    if task["acceptance_state"] != "accepted":
        raise BackupError(
            "not_field_accepted",
            "inbox cleanup requires completed Field-Replay Acceptance",
        )
    imported = db.query_one(
        "SELECT archive_path, bundle_sha256 FROM run_bundle_imports WHERE run_id = ?",
        (run_id,),
    )
    if imported is None:
        raise BackupError("import_not_found", f"no imported package for {run_id}")
    archive = Path(imported["archive_path"])
    if not archive.is_dir():
        raise BackupError("archive_missing", "Package Archive failed identity verification")
    import bagit

    from .run_bundle import _bundle_hash

    try:
        bag = bagit.Bag(str(archive))
        bag.validate(processes=1)
        current = _bundle_hash(bag)
    except (OSError, ValueError, bagit.BagError) as exc:
        raise BackupError(
            "archive_identity_mismatch",
            "Package Archive identity check failed",
        ) from exc
    if current != imported["bundle_sha256"]:
        raise BackupError("archive_identity_mismatch", "Package Archive identity check failed")
    backup = db.query_one(
        "SELECT id FROM verified_backups WHERE run_id = ? ORDER BY id DESC",
        (run_id,),
    )
    if backup is None:
        raise BackupError("backup_required", "inbox cleanup requires a Verified Backup")
    inbox = Path(import_root) / "inbox" / bundle_name
    if not inbox.is_dir():
        raise BackupError("inbox_missing", f"inbox copy {bundle_name} does not exist")
    import shutil

    shutil.rmtree(inbox)
    return {
        "status": "cleaned",
        "bundle_name": bundle_name,
        "run_id": run_id,
        "archive_retained": True,
    }


def retire_scene_version(db: Database, scene_version_id: str) -> dict:
    row = db.query_one(
        "SELECT scene_version_id FROM scene_versions WHERE scene_version_id = ?",
        (scene_version_id,),
    )
    if row is None:
        raise BackupError("scene_version_not_found", scene_version_id)
    db.execute(
        "UPDATE scene_versions SET navigation_default = 0 WHERE scene_version_id = ?",
        (scene_version_id,),
    )
    return {
        "scene_version_id": scene_version_id,
        "deleted": False,
        "navigation_default": False,
    }


def delete_scene_version(db: Database, scene_version_id: str) -> None:
    referenced = db.query_one(
        "SELECT id FROM tasks WHERE scene_version_id = ?",
        (scene_version_id,),
    )
    if referenced is not None:
        raise BackupError(
            "scene_version_in_use",
            "a Scene Version referenced by a historical run cannot be deleted",
        )
    db.execute("DELETE FROM scene_versions WHERE scene_version_id = ?", (scene_version_id,))
