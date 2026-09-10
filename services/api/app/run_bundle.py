"""Validate and atomically materialize offline RFC 8493 inspection-run bags."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import bagit
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .db import Database
from .inspection_package import (
    InspectionPackageManifest,
    InspectionPackageTrajectory,
    validate_v2_semantics,
)
from .writer_lock import ImportWriterLock, WriterLockHeld

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
GitCommit = Annotated[str, StringConstraints(pattern=r"^(unknown|[a-f0-9]{7,40})$")]

MANIFEST_PATH = Path("data/manifest.json")
MAX_JSON_BYTES = 128 * 1024 * 1024
SAFE_LABEL = re.compile(r"[^A-Za-z0-9._-]+")
_IMPORT_LOCK = threading.RLock()


class StrictModel(BaseModel):
    """Forbid undeclared fields and non-finite numeric values."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Position3D(StrictModel):
    x: float
    y: float
    z: float


class TrajectoryPoint(StrictModel):
    seq: int = Field(ge=0)
    timestamp: datetime
    position: Position3D
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    speed_mps: float | None = Field(default=None, ge=0)
    battery_pct: float | None = Field(default=None, ge=0, le=100)


class RunTrajectory(StrictModel):
    schema_version: Literal["1.0"]
    run_id: Identifier
    coordinate_system: Literal["scene_local_yup"]
    alignment_id: Identifier
    points: list[TrajectoryPoint] = Field(min_length=1, max_length=1_000_000)


class BagReference(StrictModel):
    storage_id: Literal["mcap"]
    path: str


class ReplayReference(StrictModel):
    trajectory_path: str


class BundleArtifact(StrictModel):
    path: str
    kind: Literal["evidence_image", "pointcloud", "inspection_result"]
    display: bool


class RunBundleManifest(StrictModel):
    schema_version: Literal["1.0"]
    source_kind: Literal["synthetic_contract_fixture", "inspection_run"]
    run_id: Identifier
    scene_id: Identifier
    name: ShortText
    status: Literal[
        "completed",
        "completed_with_exceptions",
        "incomplete",
        "aborted",
        "failed",
    ]
    started_at: datetime
    ended_at: datetime
    coordinate_system: Literal["scene_local_yup"]
    alignment_id: Identifier
    git_commit: GitCommit
    config_sha256: Sha256
    model_version: str | None
    bag: BagReference | None
    replay: ReplayReference
    artifacts: list[BundleArtifact]
    exit_reason: ShortText


class BundleImportError(Exception):
    """A package could not be imported without weakening integrity or provenance."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict | None = None,
        status_code: int = 400,
        stage: str = "validate",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        merged = {"stage": stage, **(details or {})}
        self.details = merged
        self.status_code = status_code


def _iso_utc(value: datetime) -> str:
    return _require_utc(value).isoformat().replace("+00:00", "Z")


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must use an explicit UTC offset")
    return value.astimezone(UTC)


def _payload_path(value: str, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError(f"{field} must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"{field} must stay inside the BagIt payload")
    return path


def _load_json(path: Path) -> dict:
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        raise ValueError(f"{path.name} exceeds the {MAX_JSON_BYTES}-byte JSON limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _assert_plain_tree(root: Path) -> None:
    """Reject links and special files before copying from removable media."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("bundle source must be a real directory")
    for current, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if any(ord(character) < 32 or ord(character) == 127 for character in relative):
                raise ValueError(f"control characters are forbidden in paths: {relative!r}")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"symbolic links are forbidden: {relative}")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"special files are forbidden: {relative}")


def _bundle_hash(bag: bagit.Bag) -> str:
    entries: list[str] = []
    for path, checksums in bag.payload_entries().items():
        checksum = checksums.get("sha256")
        if checksum is None:
            raise ValueError("every payload file must have a SHA-256 checksum")
        entries.append(f"{checksum.lower()}  {PurePosixPath(path).as_posix()}\n")
    if not entries:
        raise ValueError("BagIt payload must not be empty")
    return hashlib.sha256("".join(sorted(entries)).encode("utf-8")).hexdigest()


def _validate_v1_semantics(staged: Path, raw: dict) -> tuple[RunBundleManifest, RunTrajectory]:
    manifest = RunBundleManifest.model_validate(raw)
    started_at = _require_utc(manifest.started_at)
    ended_at = _require_utc(manifest.ended_at)
    if ended_at < started_at:
        raise ValueError("ended_at must not precede started_at")
    if manifest.source_kind == "inspection_run" and manifest.bag is None:
        raise ValueError("inspection_run packages must include an MCAP bag reference")

    config_path = staged / "data/config/system.yaml"
    if not config_path.is_file():
        raise ValueError("data/config/system.yaml is required")
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    if config_hash != manifest.config_sha256:
        raise ValueError("config_sha256 does not match data/config/system.yaml")

    referenced_paths = [manifest.replay.trajectory_path]
    if manifest.bag is not None:
        referenced_paths.append(manifest.bag.path)
    referenced_paths.extend(artifact.path for artifact in manifest.artifacts)
    for index, value in enumerate(referenced_paths):
        relative = _payload_path(value, f"referenced_paths[{index}]")
        target = staged / "data" / Path(*relative.parts)
        if not target.exists():
            raise ValueError(f"referenced payload path does not exist: {value}")

    trajectory_path = _payload_path(manifest.replay.trajectory_path, "trajectory_path")
    trajectory = RunTrajectory.model_validate(
        _load_json(staged / "data" / Path(*trajectory_path.parts))
    )
    if trajectory.run_id != manifest.run_id:
        raise ValueError("trajectory run_id does not match manifest run_id")
    if trajectory.alignment_id != manifest.alignment_id:
        raise ValueError("trajectory alignment_id does not match manifest alignment_id")

    previous_time: datetime | None = None
    for expected_seq, point in enumerate(trajectory.points):
        if point.seq != expected_seq:
            raise ValueError("trajectory seq must be contiguous and start at zero")
        point_time = _require_utc(point.timestamp)
        if not started_at <= point_time <= ended_at:
            raise ValueError("trajectory timestamps must be inside the run interval")
        if previous_time is not None and point.timestamp < previous_time:
            raise ValueError("trajectory timestamps must be monotonic")
        previous_time = point.timestamp
    return manifest, trajectory


def _validate_semantics(
    staged: Path, bag: bagit.Bag
) -> tuple[
    RunBundleManifest | InspectionPackageManifest,
    RunTrajectory | InspectionPackageTrajectory,
]:
    if bag.version_info != (1, 0):
        raise ValueError(f"BagIt-Version must be 1.0, got {bag.version_info}")
    if (staged / "fetch.txt").exists():
        raise ValueError("fetch.txt is forbidden; USB packages must be complete")
    if not (staged / "manifest-sha256.txt").is_file():
        raise ValueError("manifest-sha256.txt is required")
    raw = _load_json(staged / MANIFEST_PATH)
    schema_version = raw.get("schema_version")
    source_kind = raw.get("source_kind")
    if raw.get("status") in ("incomplete", "aborted", "failed"):
        raise BundleImportError(
            "diagnostic_package",
            "diagnostic packages are outside the import boundary",
            stage="validate",
        )
    alignment_id = None
    if isinstance(raw.get("alignment"), dict):
        alignment_id = raw["alignment"].get("alignment_id")
    else:
        alignment_id = raw.get("alignment_id")
    if schema_version == "1.0" and source_kind == "inspection_run":
        raise ValueError(
            "formal inspection_run packages must use schema_version 2.0; "
            "v1 is not production-compatible and remains only for development fixtures"
        )
    if schema_version == "1.0":
        if source_kind != "synthetic_contract_fixture":
            raise ValueError(
                "v1 packages may only be synthetic_contract_fixture development fixtures"
            )
        return _validate_v1_semantics(staged, raw)
    if schema_version == "2.0":
        if (
            source_kind == "inspection_run"
            and isinstance(alignment_id, str)
            and "synthetic" in alignment_id.lower()
        ):
            raise BundleImportError(
                "synthetic_alignment",
                "synthetic alignment cannot be used for a Formal Inspection Package",
                stage="validate",
            )
        if source_kind == "synthetic_contract_fixture":
            raise BundleImportError(
                "synthetic_package",
                "synthetic packages cannot create an Imported Run",
                stage="validate",
            )
        return validate_v2_semantics(staged, bag, raw)
    raise ValueError(f"unsupported schema_version: {schema_version}")


def _manifest_scene_id(
    manifest: RunBundleManifest | InspectionPackageManifest,
) -> str:
    scene_version = getattr(manifest, "scene_version", None)
    if scene_version is not None:
        return scene_version.scene_id
    return manifest.scene_id


def _is_recorded_run(manifest: RunBundleManifest | InspectionPackageManifest) -> bool:
    return manifest.schema_version == "2.0" and manifest.source_kind == "inspection_run"


def _task_fields(manifest: RunBundleManifest | InspectionPackageManifest) -> dict:
    package_status = (
        manifest.status if manifest.status in ("completed", "completed_with_exceptions") else None
    )
    if _is_recorded_run(manifest):
        scene_version = manifest.scene_version
        return {
            "run_kind": "recorded",
            "package_status": package_status,
            "acceptance_state": "pending_acceptance",
            "task_status": manifest.status,
            "scene_version_id": scene_version.scene_version_id,
            "alignment_id": manifest.alignment.alignment_id,
            "asset_sha256": scene_version.asset_sha256,
            "evidence_sha256": manifest.alignment.evidence_sha256,
        }
    return {
        "run_kind": "demonstration",
        "package_status": package_status,
        "acceptance_state": "not_applicable",
        "task_status": (
            "completed"
            if manifest.status in ("completed", "completed_with_exceptions")
            else "aborted"
        ),
        "scene_version_id": None,
        "alignment_id": getattr(manifest, "alignment_id", None),
        "asset_sha256": None,
        "evidence_sha256": None,
    }


def _safe_label(value: str) -> str:
    return SAFE_LABEL.sub("-", value).strip("-.")[:80] or "bundle"


def _quarantine(staged: Path, quarantine_root: Path, label: str) -> Path | None:
    if not staged.exists():
        return None
    target = quarantine_root / f"{_safe_label(label)}-{uuid.uuid4().hex[:12]}"
    os.replace(staged, target)
    return target


def _parse_archive_name(name: str) -> tuple[str, str] | None:
    if len(name) < 14 or "-" not in name:
        return None
    run_id, sha12 = name.rsplit("-", 1)
    if len(sha12) != 12 or run_id == "":
        return None
    return run_id, sha12


def _record_rejection(
    db: Database,
    exc: BundleImportError,
    *,
    inbox_name: str | None = None,
    run_id: str | None = None,
    package_sha256: str | None = None,
    schema_version: str | None = None,
    source_kind: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO import_rejections "
        "(code, stage, message, run_id, package_sha256, schema_version, source_kind, "
        "inbox_name, quarantine_path, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            exc.code,
            exc.stage,
            exc.message,
            run_id,
            package_sha256,
            schema_version,
            source_kind,
            inbox_name,
            (exc.details or {}).get("quarantine_path"),
            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
    )


def _assert_formal_landmarks(db: Database, manifest: InspectionPackageManifest) -> None:
    registered = db.query_all(
        "SELECT * FROM registered_landmarks WHERE alignment_id = ?",
        (manifest.alignment.alignment_id,),
    )
    if not registered:
        raise BundleImportError(
            "alignment_not_registered",
            "Formal Inspection Package requires registered ArUco landmarks",
            stage="validate",
            status_code=409,
        )
    package = {item.landmark_id: item for item in manifest.acceptance_evidence.landmarks}
    for row in registered:
        if row["role"] != "required":
            continue
        item = package.get(row["landmark_id"])
        if item is None:
            raise BundleImportError(
                "required_landmark_missing",
                f"required ArUco landmark {row['landmark_id']} is missing from the package",
                stage="validate",
                status_code=409,
            )
        pose = item.registered_pose
        if (
            item.marker_id != row["marker_id"]
            or item.dictionary != row["dictionary"]
            or item.role != row["role"]
            or item.min_valid_samples != row["min_valid_samples"]
            or not math.isclose(item.physical_size_m, row["physical_size_m"], abs_tol=1e-9)
            or not math.isclose(pose.position.x, row["pos_x"], abs_tol=1e-9)
            or not math.isclose(pose.position.y, row["pos_y"], abs_tol=1e-9)
            or not math.isclose(pose.position.z, row["pos_z"], abs_tol=1e-9)
            or not math.isclose(pose.rotation_rpy_deg.roll, row["roll_deg"], abs_tol=1e-9)
            or not math.isclose(pose.rotation_rpy_deg.pitch, row["pitch_deg"], abs_tol=1e-9)
            or not math.isclose(pose.rotation_rpy_deg.yaw, row["yaw_deg"], abs_tol=1e-9)
            or not math.isclose(
                item.pose_tolerance.translation_m, row["translation_m"], abs_tol=1e-9
            )
            or not math.isclose(item.pose_tolerance.rotation_deg, row["rotation_deg"], abs_tol=1e-9)
        ):
            raise BundleImportError(
                "landmark_registration_mismatch",
                f"ArUco landmark {row['landmark_id']} does not match Scene Registration",
                stage="validate",
                status_code=409,
            )


def recover_incomplete_imports(db: Database, import_root: Path | str) -> None:
    """Finish orphan archives or isolate leftover staging copies. No manual repair."""
    directories = ensure_import_directories(import_root)
    for partial in directories["staging"].glob("*.partial"):
        _quarantine(partial, directories["quarantine"], "interrupted-copy")
    for archive in directories["archive"].iterdir():
        if not archive.is_dir():
            continue
        parsed = _parse_archive_name(archive.name)
        if parsed is None:
            continue
        run_id, _sha12 = parsed
        existing = db.query_one(
            "SELECT run_id FROM run_bundle_imports WHERE run_id = ?",
            (run_id,),
        )
        if existing is not None:
            continue
        try:
            bag = bagit.Bag(str(archive))
            bag.validate(processes=1)
            manifest, trajectory = _validate_semantics(archive, bag)
            if isinstance(manifest, InspectionPackageManifest):
                _assert_formal_landmarks(db, manifest)
            _materialize(
                db,
                manifest,
                trajectory,
                bundle_sha256=_bundle_hash(bag),
                archive_path=archive,
                imported_at=datetime.now(UTC),
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
            bagit.BagError,
            BundleImportError,
        ):
            _quarantine(archive, directories["quarantine"], f"orphaned-{run_id}")


def _distance_m(trajectory: RunTrajectory | InspectionPackageTrajectory) -> float:
    distance = 0.0
    for previous, current in zip(trajectory.points, trajectory.points[1:], strict=False):
        a = previous.position
        b = current.position
        distance += math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
    return round(distance, 3)


def _materialize(
    db: Database,
    manifest: RunBundleManifest | InspectionPackageManifest,
    trajectory: RunTrajectory | InspectionPackageTrajectory,
    *,
    bundle_sha256: str,
    archive_path: Path,
    imported_at: datetime,
) -> None:
    fields = _task_fields(manifest)
    with db.transaction() as conn:
        scene_id = _manifest_scene_id(manifest)
        scene = conn.execute("SELECT id FROM scenes WHERE id = ?", (scene_id,)).fetchone()
        if scene is None:
            raise BundleImportError(
                "scene_not_found",
                f"scene {scene_id} does not exist",
                details={"scene_id": scene_id},
                status_code=409,
            )
        if fields["run_kind"] == "recorded":
            registered = conn.execute(
                "SELECT a.evidence_sha256, v.scene_id, v.asset_sha256 "
                "FROM verified_alignments a "
                "JOIN scene_versions v ON v.scene_version_id = a.scene_version_id "
                "WHERE a.alignment_id = ? AND a.scene_version_id = ?",
                (fields["alignment_id"], fields["scene_version_id"]),
            ).fetchone()
            if registered is None:
                raise BundleImportError(
                    "alignment_not_registered",
                    "Formal Inspection Package requires a pre-registered "
                    "Scene Version and Verified Alignment",
                    details={
                        "scene_version_id": fields["scene_version_id"],
                        "alignment_id": fields["alignment_id"],
                    },
                    status_code=409,
                )
            if (
                registered["scene_id"] != scene_id
                or registered["asset_sha256"] != fields["asset_sha256"]
                or registered["evidence_sha256"] != fields["evidence_sha256"]
            ):
                raise BundleImportError(
                    "scene_alignment_mismatch",
                    "package Scene Version or alignment evidence does not match registration",
                    details={
                        "scene_version_id": fields["scene_version_id"],
                        "alignment_id": fields["alignment_id"],
                    },
                    status_code=409,
                )
        if conn.execute("SELECT id FROM tasks WHERE id = ?", (manifest.run_id,)).fetchone():
            raise BundleImportError(
                "run_id_conflict",
                f"task/run id {manifest.run_id} already exists",
                details={"run_id": manifest.run_id},
                status_code=409,
            )

        has_pointcloud = False
        artifacts = getattr(manifest, "artifacts", None) or []
        has_pointcloud = any(getattr(item, "kind", None) == "pointcloud" for item in artifacts)
        conn.execute(
            "INSERT INTO tasks "
            "(id, scene_id, name, mode, status, actual_start, actual_end, distance_m, "
            "event_count, run_kind, package_status, acceptance_state, scene_version_id, "
            "alignment_id, has_pointcloud) "
            "VALUES (?, ?, ?, 'replay', ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
            (
                manifest.run_id,
                scene_id,
                manifest.name,
                fields["task_status"],
                _iso_utc(manifest.started_at),
                _iso_utc(manifest.ended_at),
                _distance_m(trajectory),
                fields["run_kind"],
                fields["package_status"],
                fields["acceptance_state"],
                fields["scene_version_id"],
                fields["alignment_id"],
                int(has_pointcloud),
            ),
        )
        conn.executemany(
            "INSERT INTO trajectory_points "
            "(task_id, seq, timestamp, x, y, z, heading_deg, speed_mps) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    manifest.run_id,
                    point.seq,
                    _iso_utc(point.timestamp),
                    point.position.x,
                    point.position.y,
                    point.position.z,
                    point.heading_deg,
                    point.speed_mps,
                )
                for point in trajectory.points
            ],
        )
        conn.execute(
            "INSERT INTO run_bundle_imports "
            "(run_id, bundle_sha256, schema_version, source_kind, archive_path, "
            "manifest_json, imported_at, package_status, run_kind, acceptance_state, "
            "scene_version_id, alignment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                manifest.run_id,
                bundle_sha256,
                manifest.schema_version,
                manifest.source_kind,
                str(archive_path),
                manifest.model_dump_json(),
                _iso_utc(imported_at),
                fields["package_status"],
                fields["run_kind"],
                fields["acceptance_state"],
                fields["scene_version_id"],
                fields["alignment_id"],
            ),
        )


def _maybe_crash(crash_after: str | None, stage: str) -> None:
    if crash_after == stage:
        raise KeyboardInterrupt(f"injected interrupt after {stage}")


def _import_run_bundle(
    db: Database,
    source: Path | str,
    import_root: Path | str,
    *,
    now: datetime | None = None,
    crash_after: str | None = None,
) -> dict:
    """Copy one removable-media bag into staging, validate, archive and materialize it."""
    source_path = Path(source)
    root = Path(import_root)
    directories = ensure_import_directories(root)
    staging_root = directories["staging"]
    archive_root = directories["archive"]
    quarantine_root = directories["quarantine"]

    stage = staging_root / f"{uuid.uuid4().hex}.partial"
    try:
        _assert_plain_tree(source_path)
        shutil.copytree(source_path, stage, symlinks=True)
        _assert_plain_tree(stage)
        _maybe_crash(crash_after, "copy")
    except KeyboardInterrupt:
        raise
    except (OSError, ValueError) as exc:
        quarantine = _quarantine(stage, quarantine_root, source_path.name)
        error = BundleImportError(
            "bundle_copy_failed",
            str(exc),
            details={"quarantine_path": str(quarantine) if quarantine else None},
            stage="copy",
        )
        _record_rejection(db, error, inbox_name=source_path.name)
        raise error from exc

    try:
        bag = bagit.Bag(str(stage))
        bag.validate(processes=1)
        manifest, trajectory = _validate_semantics(stage, bag)
        if isinstance(manifest, InspectionPackageManifest):
            _assert_formal_landmarks(db, manifest)
        bundle_sha256 = _bundle_hash(bag)
        _maybe_crash(crash_after, "validate")
    except KeyboardInterrupt:
        raise
    except BundleImportError as exc:
        quarantine = _quarantine(stage, quarantine_root, source_path.name)
        exc.details["quarantine_path"] = str(quarantine) if quarantine else None
        _record_rejection(db, exc, inbox_name=source_path.name)
        raise
    except (OSError, ValueError, json.JSONDecodeError, ValidationError, bagit.BagError) as exc:
        quarantine = _quarantine(stage, quarantine_root, source_path.name)
        error = BundleImportError(
            "bundle_invalid",
            str(exc),
            details={"quarantine_path": str(quarantine) if quarantine else None},
            stage="validate",
        )
        _record_rejection(db, error, inbox_name=source_path.name)
        raise error from exc

    existing = db.query_one(
        "SELECT bundle_sha256, archive_path FROM run_bundle_imports WHERE run_id = ?",
        (manifest.run_id,),
    )
    if existing is not None:
        if existing["bundle_sha256"] != bundle_sha256:
            quarantine = _quarantine(stage, quarantine_root, manifest.run_id)
            error = BundleImportError(
                "run_id_conflict",
                f"run_id {manifest.run_id} was already imported with different content",
                details={"quarantine_path": str(quarantine) if quarantine else None},
                status_code=409,
                stage="validate",
            )
            _record_rejection(
                db,
                error,
                inbox_name=source_path.name,
                run_id=manifest.run_id,
                package_sha256=bundle_sha256,
                schema_version=manifest.schema_version,
                source_kind=manifest.source_kind,
            )
            raise error
        shutil.rmtree(stage)
        count = db.query_one(
            "SELECT COUNT(*) AS count FROM trajectory_points WHERE task_id = ?",
            (manifest.run_id,),
        )["count"]
        return {
            "run_id": manifest.run_id,
            "bundle_sha256": bundle_sha256,
            "status": "duplicate",
            "trajectory_points": count,
        }

    archive = archive_root / f"{manifest.run_id}-{bundle_sha256[:12]}"
    if archive.exists():
        ledger = db.query_one(
            "SELECT bundle_sha256 FROM run_bundle_imports WHERE run_id = ?",
            (manifest.run_id,),
        )
        if ledger is None:
            shutil.rmtree(stage)
            recover_incomplete_imports(db, root)
            count = db.query_one(
                "SELECT COUNT(*) AS count FROM trajectory_points WHERE task_id = ?",
                (manifest.run_id,),
            )
            return {
                "run_id": manifest.run_id,
                "bundle_sha256": bundle_sha256,
                "status": "imported",
                "trajectory_points": 0 if count is None else count["count"],
            }
        quarantine = _quarantine(stage, quarantine_root, manifest.run_id)
        error = BundleImportError(
            "archive_conflict",
            f"archive path already exists for run {manifest.run_id}",
            details={"quarantine_path": str(quarantine)},
            status_code=409,
            stage="archive",
        )
        _record_rejection(db, error, inbox_name=source_path.name, run_id=manifest.run_id)
        raise error
    os.replace(stage, archive)
    _maybe_crash(crash_after, "archive")
    _maybe_crash(crash_after, "ledger")

    try:
        _maybe_crash(crash_after, "trajectory")
        _materialize(
            db,
            manifest,
            trajectory,
            bundle_sha256=bundle_sha256,
            archive_path=archive,
            imported_at=now or datetime.now(UTC),
        )
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        _quarantine(archive, quarantine_root, manifest.run_id)
        if isinstance(exc, BundleImportError):
            _record_rejection(
                db,
                exc,
                inbox_name=source_path.name,
                run_id=manifest.run_id,
                package_sha256=bundle_sha256,
            )
        raise

    return {
        "run_id": manifest.run_id,
        "bundle_sha256": bundle_sha256,
        "status": "imported",
        "trajectory_points": len(trajectory.points),
    }


def import_run_bundle(
    db: Database,
    source: Path | str,
    import_root: Path | str,
    *,
    now: datetime | None = None,
    crash_after: str | None = None,
) -> dict:
    """Serialize the filesystem/SQLite import boundary within one server process."""
    try:
        with ImportWriterLock(import_root):
            recover_incomplete_imports(db, import_root)
            with _IMPORT_LOCK:
                return _import_run_bundle(db, source, import_root, now=now, crash_after=crash_after)
    except WriterLockHeld as exc:
        raise BundleImportError("import_lock_held", str(exc), stage="lock") from exc


def import_inbox_directory(
    db: Database,
    import_root: Path | str,
    bundle_name: str,
    *,
    now: datetime | None = None,
    crash_after: str | None = None,
) -> dict:
    """Import one inbox directory by name. Rejects filesystem paths."""
    if not bundle_name or Path(bundle_name).name != bundle_name or bundle_name in (".", ".."):
        raise BundleImportError(
            "invalid_bundle_name",
            "bundle_name must be one inbox directory name",
        )
    root = Path(import_root)
    source = root / "inbox" / bundle_name
    if not source.is_dir():
        raise BundleImportError(
            "bundle_not_found",
            f"inbox bundle {bundle_name} does not exist",
            status_code=404,
        )
    return import_run_bundle(db, source, root, now=now, crash_after=crash_after)


def list_run_bundle_imports(db: Database) -> list[dict]:
    rows = db.query_all(
        "SELECT run_id, bundle_sha256, schema_version, source_kind, imported_at, "
        "package_status, run_kind, acceptance_state, scene_version_id, alignment_id "
        "FROM run_bundle_imports ORDER BY imported_at DESC, run_id ASC"
    )
    return [dict(row) for row in rows]


def list_rejections(db: Database) -> list[dict]:
    rows = db.query_all(
        "SELECT id, code, stage, message, run_id, package_sha256, schema_version, "
        "source_kind, inbox_name, quarantine_path, created_at "
        "FROM import_rejections ORDER BY id DESC, created_at DESC"
    )
    return [dict(row) for row in rows]


def ensure_import_directories(import_root: Path | str) -> dict[str, Path]:
    root = Path(import_root)
    directories = {name: root / name for name in ("inbox", "staging", "archive", "quarantine")}
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    return directories
