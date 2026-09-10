"""Seed Formal Inspection Package v2 Recorded Runs for Playwright."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import DEMO_DIR, Settings, assert_durable_paths
from app.db import Database
from app.run_bundle import import_inbox_directory
from app.scene_registration import register_scene_from_file
from app.seed import seed_if_empty

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRATION = REPO_ROOT / "data/contract-fixtures/scene-registration-scene-001-v1.json"
COMPLETED = REPO_ROOT / "data/contract-fixtures/inspection-package-v2-completed"
EXCEPTIONS = REPO_ROOT / "data/contract-fixtures/inspection-package-v2-completed-with-exceptions"


def main() -> None:
    settings = Settings.from_env()
    assert_durable_paths(settings)
    db = Database(settings.db_path)
    db.init_schema()
    seed_if_empty(db, DEMO_DIR)
    existing = db.query_one(
        "SELECT run_id FROM run_bundle_imports WHERE run_id = ?",
        ("golden-run-v2-completed",),
    )
    if existing is not None:
        db.close()
        return
    register_scene_from_file(db, REGISTRATION, operator_label="playwright")
    inbox = Path(settings.import_root) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for source, name in (
        (COMPLETED, "v2-completed"),
        (EXCEPTIONS, "v2-exceptions"),
    ):
        target = inbox / name
        if not target.exists():
            shutil.copytree(source, target)
        import_inbox_directory(db, settings.import_root, name)
    db.close()


if __name__ == "__main__":
    main()
