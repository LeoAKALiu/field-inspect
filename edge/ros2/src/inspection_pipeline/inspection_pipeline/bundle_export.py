"""Export a finalized inspection run to an RFC 8493 BagIt directory on USB."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from ament_index_python.packages import get_package_share_directory

from inspection_pipeline.bagit import BundleVerificationError, verify_bagit_bundle
from inspection_pipeline.fixed_route import FixedRouteError, load_fixed_route
from inspection_pipeline.pointcloud import PointCloudError, validate_pointcloud_manifest
from inspection_pipeline.run_manifest import (
    ROUTE_EXECUTION_STATES,
    stop_outcome_from_route,
)
from inspection_pipeline.station_attempts import StationEvidenceError
from inspection_pipeline.station_attempts_export import validate_station_attempts_artifact
from inspection_pipeline.trajectory import TrajectoryError, validate_trajectory_document
from inspection_pipeline.trajectory_export import load_scene_alignment

BUNDLE_SCHEMA_VERSION = "1.0"  # Internal operational/provenance schema; formal transfer is v2.
BAGIT_VERSION = "1.0"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TERMINAL_STATUSES = {
    "completed",
    "completed_with_exceptions",
    "incomplete",
    "aborted",
    "failed",
}
DEFAULT_TRANSFER_RESERVE_BYTES = 1024 * 1024 * 1024


class BundleExportError(ValueError):
    """A run cannot be represented as a complete offline transfer bag."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleExportError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BundleExportError(f"{path} must contain a JSON object")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise BundleExportError(f"{field} is not a valid identifier")
    return value


def _utc_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise BundleExportError(f"{field} must be an ISO 8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleExportError(f"{field} must be an ISO 8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise BundleExportError(f"{field} must use an explicit UTC offset")
    return value


def _assert_plain_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise BundleExportError(f"{root} must be a real directory")
    for current, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if any(ord(character) < 32 or ord(character) == 127 for character in relative):
                raise BundleExportError(f"control characters are forbidden in paths: {relative!r}")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise BundleExportError(f"symbolic links are forbidden: {relative}")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise BundleExportError(f"special files are forbidden: {relative}")


def _copy_payload_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    _assert_plain_tree(source)
    shutil.copytree(source, destination)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _build_semantic_manifest(
    operational: dict[str, Any],
    *,
    run_dir: Path,
    scene_id: str,
    alignment_id: str,
    name: Optional[str],
) -> dict[str, Any]:
    run_id = _identifier(operational.get("run_id"), "run_id")
    if operational.get("run_mode") != "production":
        raise BundleExportError(
            "only production inspection runs can be exported as delivery evidence"
        )
    status = operational.get("status")
    if status not in TERMINAL_STATUSES:
        raise BundleExportError(f"run {run_id} is not finalized: {status}")
    started_at = _utc_timestamp(operational.get("started_at"), "started_at")
    ended_at = _utc_timestamp(operational.get("ended_at"), "ended_at")
    if datetime.fromisoformat(ended_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        started_at.replace("Z", "+00:00")
    ):
        raise BundleExportError("ended_at must not precede started_at")

    config_path = run_dir / "config/system.yaml"
    if not config_path.is_file():
        raise BundleExportError("finalized run is missing config/system.yaml")
    config_sha256 = _sha256(config_path)
    if operational.get("config_sha256") != config_sha256:
        raise BundleExportError(
            "operational manifest config_sha256 does not match config/system.yaml"
        )

    route_snapshot_path = run_dir / "config/fixed_route.yaml"
    route_reference = operational.get("fixed_route")
    if route_reference is None:
        if route_snapshot_path.exists():
            raise BundleExportError(
                "fixed route snapshot exists without operational manifest provenance"
            )
    else:
        if not isinstance(route_reference, dict) or set(route_reference) != {
            "route_id",
            "source_path",
            "path",
            "sha256",
            "verified",
        }:
            raise BundleExportError("operational manifest fixed_route is invalid")
        if route_reference.get("path") != "config/fixed_route.yaml":
            raise BundleExportError("fixed route snapshot path is invalid")
        source_path = route_reference.get("source_path")
        if (
            not isinstance(source_path, str)
            or not source_path
            or not Path(source_path).is_absolute()
        ):
            raise BundleExportError("fixed route source_path is invalid")
        if not route_snapshot_path.is_file():
            raise BundleExportError("operational manifest fixed_route snapshot is missing")
        if route_reference.get("sha256") != _sha256(route_snapshot_path):
            raise BundleExportError("fixed route snapshot SHA-256 mismatch")
        try:
            fixed_route = load_fixed_route(route_snapshot_path)
        except FixedRouteError as exc:
            raise BundleExportError(f"fixed route is not approved for export: {exc}") from exc
        if fixed_route.route_id != route_reference.get("route_id"):
            raise BundleExportError("fixed route route_id mismatch")
        if route_reference.get("verified") is not True:
            raise BundleExportError("fixed route manifest provenance is not verified")

    route_execution = operational.get("route_execution")
    if route_execution is not None:
        if route_reference is None:
            raise BundleExportError("route execution is missing fixed route provenance")
        if not isinstance(route_execution, dict) or set(route_execution) != {
            "execution_id",
            "execution_started_ns",
            "state",
            "reason",
            "attempts",
        }:
            raise BundleExportError("operational manifest route_execution is invalid")
        _identifier(route_execution.get("execution_id"), "route_execution.execution_id")
        if (
            not isinstance(route_execution.get("execution_started_ns"), int)
            or isinstance(route_execution.get("execution_started_ns"), bool)
            or route_execution["execution_started_ns"] <= 0
        ):
            raise BundleExportError("route_execution.execution_started_ns is invalid")
        run_started_ns = int(
            datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
            * 1_000_000_000
        )
        if route_execution["execution_started_ns"] < run_started_ns:
            raise BundleExportError("route execution predates the inspection run")
        if route_execution.get("state") not in ROUTE_EXECUTION_STATES:
            raise BundleExportError("route_execution.state is invalid")
        if not isinstance(route_execution.get("reason"), str) or not route_execution["reason"]:
            raise BundleExportError("route_execution.reason is invalid")
        if (
            not isinstance(route_execution.get("attempts"), int)
            or isinstance(route_execution.get("attempts"), bool)
            or route_execution["attempts"] <= 0
        ):
            raise BundleExportError("route_execution.attempts is invalid")
    expected_status, _expected_reason = stop_outcome_from_route(
        route_execution,
        route_required=route_reference is not None,
    )
    if status in {"completed", "completed_with_exceptions"} and status != expected_status:
        raise BundleExportError(
            "operational status contradicts the supervised route terminal state"
        )

    trajectory_path = run_dir / "replay/trajectory.json"
    trajectory = _load_json(trajectory_path)
    try:
        validate_trajectory_document(trajectory)
    except TrajectoryError as exc:
        raise BundleExportError(f"invalid replay trajectory: {exc}") from exc
    if trajectory.get("run_id") != run_id:
        raise BundleExportError("trajectory run_id does not match operational manifest")
    if trajectory.get("alignment_id") != alignment_id:
        raise BundleExportError("trajectory alignment_id does not match export alignment_id")

    provenance = _load_json(run_dir / "replay/trajectory.provenance.json")
    if provenance.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise BundleExportError("trajectory provenance schema_version must be 1.0")
    if provenance.get("run_id") != run_id:
        raise BundleExportError("trajectory provenance run_id mismatch")
    if provenance.get("coordinate_system") != "scene_local_yup":
        raise BundleExportError("trajectory provenance coordinate system mismatch")
    if provenance.get("trajectory_sha256") != _sha256(trajectory_path):
        raise BundleExportError("trajectory provenance SHA-256 mismatch")
    if provenance.get("point_count") != len(trajectory["points"]):
        raise BundleExportError("trajectory provenance point_count mismatch")
    if provenance.get("extrinsics_verified") is not True:
        raise BundleExportError("trajectory provenance requires verified extrinsics")
    alignment = provenance.get("alignment")
    if not isinstance(alignment, dict):
        raise BundleExportError("trajectory provenance alignment is missing")
    if alignment.get("alignment_id") != alignment_id:
        raise BundleExportError("trajectory provenance alignment_id mismatch")
    if alignment.get("verified") is not True:
        raise BundleExportError("trajectory scene alignment must be verified")
    if alignment.get("source_kind") != "onsite_verified":
        raise BundleExportError(
            "synthetic scene alignment cannot be exported as an inspection run"
        )
    alignment_path = run_dir / "config/scene_alignment.yaml"
    if not alignment_path.is_file():
        raise BundleExportError("finalized run is missing config/scene_alignment.yaml")
    if alignment.get("sha256") != _sha256(alignment_path):
        raise BundleExportError("trajectory scene alignment SHA-256 mismatch")
    try:
        verified_alignment, _alignment_document = load_scene_alignment(alignment_path)
    except TrajectoryError as exc:
        raise BundleExportError(f"invalid scene alignment: {exc}") from exc
    if verified_alignment.alignment_id != alignment_id:
        raise BundleExportError("scene alignment file alignment_id mismatch")
    if verified_alignment.source_sha256 != alignment.get("sha256"):
        raise BundleExportError("scene alignment provenance does not match file")

    bag_dir = run_dir / "bag" / run_id
    if not bag_dir.is_dir():
        raise BundleExportError(f"finalized run is missing bag/{run_id}")
    bag_reference = operational.get("bag")
    if (
        not isinstance(bag_reference, dict)
        or bag_reference.get("storage_id") != "mcap"
    ):
        raise BundleExportError("operational manifest bag storage_id must be mcap")
    metadata_path = bag_dir / "metadata.yaml"
    try:
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        metadata_storage = metadata["rosbag2_bagfile_information"][
            "storage_identifier"
        ]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise BundleExportError(f"cannot validate rosbag metadata: {exc}") from exc
    if metadata_storage != "mcap":
        raise BundleExportError("rosbag metadata storage_identifier must be mcap")
    exit_reason = operational.get("exit_reason")
    if not isinstance(exit_reason, str) or not exit_reason.strip():
        raise BundleExportError("finalized run must have an explicit exit_reason")

    git_commit = operational.get("git_commit")
    if not isinstance(git_commit, str) or not re.fullmatch(r"unknown|[a-f0-9]{7,40}", git_commit):
        raise BundleExportError("git_commit must be unknown or a 7-40 character lowercase hash")

    bundle_name = name or f"Field Inspect run {run_id}"
    if not bundle_name.strip() or len(bundle_name) > 200:
        raise BundleExportError("name must contain 1-200 characters")

    artifacts = []
    pointcloud_root = run_dir / "artifacts/pointcloud"
    if pointcloud_root.exists():
        if pointcloud_root.is_symlink() or not pointcloud_root.is_dir():
            raise BundleExportError("point-cloud artifact root must be a real directory")
        pointcloud_manifest = _load_json(pointcloud_root / "manifest.json")
        fast_lio_path = run_dir / "config/fast_lio.yaml"
        export_config_path = run_dir / "config/pointcloud_export.yaml"
        if not fast_lio_path.is_file() or fast_lio_path.is_symlink():
            raise BundleExportError("point-cloud artifact is missing config/fast_lio.yaml")
        if not export_config_path.is_file() or export_config_path.is_symlink():
            raise BundleExportError(
                "point-cloud artifact is missing config/pointcloud_export.yaml"
            )
        contract_lock = _load_json(
            Path(get_package_share_directory("inspection_pipeline"))
            / "config/run-bundle.lock.json"
        )
        try:
            paths = validate_pointcloud_manifest(
                pointcloud_manifest,
                pointcloud_root=pointcloud_root,
                run_id=run_id,
                alignment_id=alignment_id,
                alignment_sha256=_sha256(alignment_path),
                alignment_translation=verified_alignment.transform.translation,
                alignment_rotation=verified_alignment.transform.rotation,
                system_sha256=config_sha256,
                fast_lio_sha256=_sha256(fast_lio_path),
                export_config_sha256=_sha256(export_config_path),
                storage_id="mcap",
                contract_authority=contract_lock["authority"],
            )
        except PointCloudError as exc:
            raise BundleExportError(f"invalid point-cloud artifact: {exc}") from exc
        pointcloud_alignment = pointcloud_manifest.get("alignment", {})
        if pointcloud_alignment.get("source_kind") != "onsite_verified":
            raise BundleExportError(
                "synthetic point-cloud alignment cannot be exported as field evidence"
            )
        artifacts = [
            {"path": path, "kind": "pointcloud", "display": True}
            for path in paths
        ]

    try:
        station_path = validate_station_attempts_artifact(run_dir)
    except StationEvidenceError as exc:
        raise BundleExportError(f"invalid station evidence: {exc}") from exc
    if station_path is not None:
        artifacts.append({"path": station_path, "kind": "inspection_result", "display": False})

    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source_kind": "inspection_run",
        "run_id": run_id,
        "scene_id": _identifier(scene_id, "scene_id"),
        "name": bundle_name,
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "coordinate_system": "scene_local_yup",
        "alignment_id": _identifier(alignment_id, "alignment_id"),
        "git_commit": git_commit,
        "config_sha256": config_sha256,
        "model_version": operational.get("model_version"),
        "bag": {"storage_id": "mcap", "path": f"bag/{run_id}"},
        "replay": {"trajectory_path": "replay/trajectory.json"},
        "artifacts": artifacts,
        "exit_reason": exit_reason,
    }


def _payload_files(root: Path) -> list[Path]:
    files = [path for path in (root / "data").rglob("*") if path.is_file()]
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _write_bagit_tags(root: Path, run_id: str, ended_at: str) -> None:
    files = _payload_files(root)
    payload_bytes = sum(path.stat().st_size for path in files)
    manifest_lines = [
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n" for path in files
    ]
    (root / "bagit.txt").write_text(
        f"BagIt-Version: {BAGIT_VERSION}\nTag-File-Character-Encoding: UTF-8\n",
        encoding="utf-8",
    )
    bagging_date = datetime.fromisoformat(ended_at.replace("Z", "+00:00")).date().isoformat()
    (root / "bag-info.txt").write_text(
        "Bag-Software-Agent: scout-inspection-bundle 1.0\n"
        f"Bagging-Date: {bagging_date}\n"
        f"External-Identifier: {run_id}\n"
        "Internal-Sender-Description: Offline SCOUT Mini inspection run\n"
        f"Payload-Oxum: {payload_bytes}.{len(files)}\n"
        "Source-Organization: SCOUT Mini project\n",
        encoding="utf-8",
    )
    (root / "manifest-sha256.txt").write_text("".join(manifest_lines), encoding="utf-8")
    tag_files = ("bag-info.txt", "bagit.txt", "manifest-sha256.txt")
    tag_lines = [f"{_sha256(root / name)}  {name}\n" for name in sorted(tag_files)]
    (root / "tagmanifest-sha256.txt").write_text("".join(tag_lines), encoding="utf-8")


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            try:
                os.fsync(descriptor)
            except OSError as exc:
                if not path.is_dir() or exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                    raise
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
    finally:
        os.close(descriptor)


def _make_transfer_readable(root: Path) -> None:
    """Avoid carrying Jetson-only owner permissions onto removable media."""
    for path in [root, *root.rglob("*")]:
        mode = 0o755 if path.is_dir() else 0o644
        try:
            path.chmod(mode)
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP):
                raise


def _tree_size_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _run_payload_hashes(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for directory in ("config", "replay", "recording", "bag", "artifacts", "acceptance", "alignment", "scene"):
        subtree = root / directory
        if not subtree.exists():
            continue
        for path in subtree.rglob("*"):
            if path.is_file():
                values[path.relative_to(root).as_posix()] = _sha256(path)
    return values


def export_run_bundle(
    run_dir: Path | str,
    destination_root: Path | str,
    *,
    scene_id: str,
    alignment_id: str,
    name: Optional[str] = None,
    reserve_free_bytes: int = DEFAULT_TRANSFER_RESERVE_BYTES,
    allow_incomplete: bool = False,
    require_station_attempts: bool = False,
) -> Path:
    """Copy one finalized run directly to a durable, atomically named USB BagIt directory."""
    unresolved_source = Path(run_dir).expanduser()
    unresolved_destination = Path(destination_root).expanduser()
    if unresolved_source.is_symlink() or unresolved_destination.is_symlink():
        raise BundleExportError("run and destination must not be symbolic links")
    source = unresolved_source.resolve()
    destination_parent = unresolved_destination.resolve()
    _assert_plain_tree(source)
    if not destination_parent.is_dir():
        raise BundleExportError(f"destination root does not exist: {destination_parent}")
    if destination_parent == source or destination_parent.is_relative_to(source):
        raise BundleExportError("destination root must not be inside the source run")
    if reserve_free_bytes < 0:
        raise BundleExportError("reserve_free_bytes must not be negative")
    source_bytes = _tree_size_bytes(source)
    effective_reserve = max(reserve_free_bytes, source_bytes // 20)
    required_bytes = source_bytes + effective_reserve
    free_bytes = shutil.disk_usage(destination_parent).free
    if free_bytes < required_bytes:
        raise BundleExportError(
            f"insufficient destination space: free={free_bytes}, required={required_bytes}"
        )

    # Local import avoids a module cycle: the validator reuses the semantic
    # contract below, while every production export must still pass it first.
    from inspection_pipeline.run_validation import (  # pylint: disable=import-outside-toplevel
        RunValidationError,
        validate_run_directory,
    )

    try:
        validation = validate_run_directory(
            source, require_station_attempts=require_station_attempts,
        )
    except RunValidationError as exc:
        raise BundleExportError(f"run validation failed: {exc}") from exc
    if (
        validation["status"] not in {"completed", "completed_with_exceptions"}
        and not allow_incomplete
    ):
        raise BundleExportError(
            "diagnostic export of an incomplete run requires --allow-incomplete"
        )
    validated_payload_hashes = {
        item["path"]: item["sha256"]
        for item in validation["files"]
        if item["path"].split("/", 1)[0]
        in {"config", "replay", "recording", "bag", "artifacts", "acceptance", "alignment", "scene"}
    }
    validated_manifest_sha256 = next(
        item["sha256"] for item in validation["files"] if item["path"] == "manifest.json"
    )

    operational = _load_json(source / "manifest.json")
    if _sha256(source / "manifest.json") != validated_manifest_sha256:
        raise BundleExportError("operational manifest changed during USB export")
    semantic = _build_semantic_manifest(
        operational,
        run_dir=source,
        scene_id=scene_id,
        alignment_id=alignment_id,
        name=name,
    )
    destination = destination_parent / f"scout-run-{semantic['run_id']}.bag"
    if destination.exists():
        raise BundleExportError(f"destination already exists: {destination}")

    temporary = Path(tempfile.mkdtemp(prefix=".scout-run-", dir=destination_parent))
    try:
        data = temporary / "data"
        _write_json(data / "manifest.json", semantic)
        _write_json(data / "metadata/jetson-manifest.json", operational)
        _copy_payload_tree(source / "config", data / "config")
        _copy_payload_tree(source / "replay", data / "replay")
        _copy_payload_tree(source / "recording", data / "recording")
        _copy_payload_tree(source / "bag" / semantic["run_id"], data / "bag" / semantic["run_id"])
        _copy_payload_tree(source / "artifacts", data / "artifacts")
        for directory in ("acceptance", "alignment", "scene"):
            _copy_payload_tree(source / directory, data / directory)
        if _run_payload_hashes(source) != validated_payload_hashes:
            raise BundleExportError("source run changed during USB export")
        if _run_payload_hashes(data) != validated_payload_hashes:
            raise BundleExportError("copied run payload differs from validated source")
        formal = semantic["status"] in {"completed", "completed_with_exceptions"}
        if formal:
            from inspection_pipeline.formal_package import upgrade_formal_payload
            try:
                semantic = upgrade_formal_payload(data, semantic)
            except (ValueError, OSError) as exc:
                raise BundleExportError(f"formal v2 inputs missing or invalid: {exc}") from exc
            _write_json(data / "manifest.json", semantic)
        _write_bagit_tags(temporary, semantic["run_id"], semantic["ended_at"])
        if formal:
            from inspection_pipeline.formal_package import validate_formal_bundle
            try:
                validate_formal_bundle(temporary)
            except ValueError as exc:
                raise BundleExportError(f"formal v2 validation failed: {exc}") from exc
        _make_transfer_readable(temporary)
        try:
            verify_bagit_bundle(temporary)
        except BundleVerificationError as exc:
            raise BundleExportError(f"generated BagIt bundle is invalid: {exc}") from exc
        _fsync_tree(temporary)
        os.replace(temporary, destination)
        parent_descriptor = os.open(destination_parent, os.O_RDONLY)
        try:
            try:
                os.fsync(parent_descriptor)
            except OSError as exc:
                if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                    raise
        finally:
            os.close(parent_descriptor)
        try:
            verify_bagit_bundle(destination)
        except BundleVerificationError as exc:
            raise BundleExportError(
                f"USB read-back verification failed for {destination}: {exc}"
            ) from exc
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return destination


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="finalized Jetson inspection run directory")
    parser.add_argument("destination_root", type=Path, help="mounted USB destination directory")
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--alignment-id", required=True)
    parser.add_argument("--name")
    parser.add_argument(
        "--reserve-free-bytes",
        type=int,
        default=DEFAULT_TRANSFER_RESERVE_BYTES,
        help="bytes that must remain free after the estimated copy",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="permit diagnostic export without changing the incomplete status",
    )
    parser.add_argument(
        "--require-station-attempts", action="store_true",
        help="require the station attempt evidence index for this delivery",
    )
    args = parser.parse_args(argv)
    destination = export_run_bundle(
        args.run_dir,
        args.destination_root,
        scene_id=args.scene_id,
        alignment_id=args.alignment_id,
        name=args.name,
        reserve_free_bytes=args.reserve_free_bytes,
        allow_incomplete=args.allow_incomplete,
        require_station_attempts=args.require_station_attempts,
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
