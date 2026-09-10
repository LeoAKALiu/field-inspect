"""Server-local CLI for Scene Registration, Import, acceptance, backup, and cleanup."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .acceptance import AcceptanceError, accept_run, withdraw_acceptance
from .backup import BackupError, cleanup_inbox, retire_scene_version, verify_backup
from .config import DEMO_DIR, Settings, assert_durable_paths
from .db import Database
from .run_bundle import BundleImportError, import_inbox_directory, list_rejections
from .scene_registration import SceneRegistrationError, register_scene_from_file
from .seed import seed_if_empty
from .writer_lock import ImportWriterLock, WriterLockHeld


def _connect(settings: Settings) -> Database:
    assert_durable_paths(settings)
    db = Database(settings.db_path)
    db.init_schema()
    seed_if_empty(db, DEMO_DIR)
    return db


def _print_error(exc: BaseException) -> int:
    details = dict(getattr(exc, "details", None) or {})
    stage = getattr(exc, "stage", None)
    if stage is not None:
        details.setdefault("stage", stage)
    payload: dict[str, Any] = {
        "error": {
            "code": getattr(exc, "code", "command_failed"),
            "message": getattr(exc, "message", str(exc)),
            "details": details,
        }
    }
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return 1


def _with_writer(settings: Settings, action: Callable[[], Any]) -> Any:
    try:
        with ImportWriterLock(settings.import_root):
            return action()
    except WriterLockHeld as exc:
        raise BundleImportError("import_lock_held", str(exc), stage="lock") from exc


def register_scene_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = _connect(settings)
    try:
        result = _with_writer(
            settings,
            lambda: register_scene_from_file(
                db,
                args.file,
                operator_label=args.operator_label,
            ),
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        SceneRegistrationError,
        BundleImportError,
    ) as exc:
        return _print_error(exc)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


def import_package_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = _connect(settings)
    try:
        result = import_inbox_directory(db, settings.import_root, args.bundle_name)
    except BundleImportError as exc:
        return _print_error(exc)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


def accept_run_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = _connect(settings)
    try:
        result = _with_writer(
            settings,
            lambda: accept_run(
                db,
                args.run_id,
                operator_label=args.operator_label,
                first_frame=args.first_frame,
                last_frame=args.last_frame,
                remarks=args.remarks,
            ),
        )
    except (AcceptanceError, BundleImportError) as exc:
        return _print_error(exc)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


def withdraw_acceptance_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = _connect(settings)
    try:
        result = _with_writer(
            settings,
            lambda: withdraw_acceptance(
                db,
                args.run_id,
                operator_label=args.operator_label,
                reason=args.reason,
            ),
        )
    except (AcceptanceError, BundleImportError) as exc:
        return _print_error(exc)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


def verify_backup_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = _connect(settings)
    try:
        result = _with_writer(
            settings,
            lambda: verify_backup(
                db,
                args.run_id,
                args.backup_root,
                live_db=Path(settings.db_path),
            ),
        )
    except (BackupError, BundleImportError) as exc:
        return _print_error(exc)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cleanup_inbox_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = _connect(settings)
    try:
        result = _with_writer(
            settings,
            lambda: cleanup_inbox(
                db,
                settings.import_root,
                args.bundle_name,
                args.run_id,
            ),
        )
    except (BackupError, BundleImportError) as exc:
        return _print_error(exc)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


def retire_scene_version_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = _connect(settings)
    try:
        result = _with_writer(
            settings,
            lambda: retire_scene_version(db, args.scene_version_id),
        )
    except (BackupError, BundleImportError) as exc:
        return _print_error(exc)
    finally:
        db.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


def list_rejections_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    db = _connect(settings)
    try:
        print(json.dumps(list_rejections(db), ensure_ascii=False))
    finally:
        db.close()
    return 0


def serve_evidence_report_command(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    assert_durable_paths(settings)
    import uvicorn

    from .local_report import create_local_app

    uvicorn.run(create_local_app(settings), host="127.0.0.1", port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Server-local Digital Twin admin CLI. Not an HTTP upload.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser(
        "register-scene",
        help="Register an immutable Scene Version and Verified Alignment",
    )
    register.add_argument("--file", required=True, type=Path, help="Registration JSON path")
    register.add_argument(
        "--operator-label",
        required=True,
        help="Self-reported Operator Label (not authenticated identity)",
    )
    register.set_defaults(func=register_scene_command)

    import_cmd = sub.add_parser(
        "import-package",
        help="Import one complete BagIt directory from the local inbox",
    )
    import_cmd.add_argument(
        "bundle_name",
        help="Inbox directory name only; not a filesystem path",
    )
    import_cmd.set_defaults(func=import_package_command)

    accept = sub.add_parser(
        "accept-run",
        help="Write a Field-Replay Acceptance record for one Recorded Run",
    )
    accept.add_argument("run_id")
    accept.add_argument(
        "--operator-label",
        required=True,
        help="Self-reported Operator Label (not authenticated identity)",
    )
    accept.add_argument("--first-frame", required=True, choices=("pass", "fail"))
    accept.add_argument("--last-frame", required=True, choices=("pass", "fail"))
    accept.add_argument("--remarks", default="")
    accept.set_defaults(func=accept_run_command)

    withdraw = sub.add_parser(
        "withdraw-acceptance",
        help="Append an Acceptance Withdrawal; does not rewrite history",
    )
    withdraw.add_argument("run_id")
    withdraw.add_argument(
        "--operator-label",
        required=True,
        help="Self-reported Operator Label (not authenticated identity)",
    )
    withdraw.add_argument("--reason", required=True)
    withdraw.set_defaults(func=withdraw_acceptance_command)

    backup = sub.add_parser(
        "verify-backup",
        help="Identity-check an independent Verified Backup (different disk)",
    )
    backup.add_argument("run_id")
    backup.add_argument("--backup-root", required=True, type=Path)
    backup.set_defaults(func=verify_backup_command)

    cleanup = sub.add_parser(
        "cleanup-inbox",
        help="Delete one Inbox Copy after archive, acceptance, and Verified Backup",
    )
    cleanup.add_argument("bundle_name")
    cleanup.add_argument("--run-id", required=True)
    cleanup.set_defaults(func=cleanup_inbox_command)

    retire = sub.add_parser(
        "retire-scene-version",
        help="Remove a Scene Version from default navigation without deleting it",
    )
    retire.add_argument("scene_version_id")
    retire.set_defaults(func=retire_scene_version_command)

    rejections = sub.add_parser(
        "list-rejections",
        help="List structured Rejected Package records",
    )
    rejections.set_defaults(func=list_rejections_command)

    report = sub.add_parser(
        "serve-evidence-report",
        help="Serve the loopback-only Local Evidence Report on 127.0.0.1",
    )
    report.add_argument("--port", type=int, default=8090)
    report.set_defaults(func=serve_evidence_report_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
