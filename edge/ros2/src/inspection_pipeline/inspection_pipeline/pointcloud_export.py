"""Generate bounded scene-aligned PCD chunks from a finalized MCAP run."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml
from ament_index_python.packages import (
    PackageNotFoundError,
    get_package_prefix,
    get_package_share_directory,
)

from inspection_pipeline.pointcloud import (
    PointCloudError,
    sha256,
    validate_pointcloud_manifest,
)
from inspection_pipeline.trajectory import TrajectoryError
from inspection_pipeline.trajectory_export import (
    _json_bytes,
    _load_json,
    _load_run_contract,
    _write_atomic,
    load_scene_alignment,
)

SOURCE_TYPE = "sensor_msgs/msg/PointCloud2"
OUTPUT_RELATIVE_ROOT = "artifacts/pointcloud"
SETTING_KEYS = {
    "schema_version",
    "source_topic",
    "chunk_duration_ns",
    "voxel_leaf_m",
    "max_input_points_per_message",
    "max_input_points_per_chunk",
    "max_output_points_per_chunk",
    "max_chunks",
    "max_duration_ns",
    "max_abs_coordinate_m",
}
MVS_LIBRARY_PATHS = frozenset({"/opt/MVS/lib/aarch64", "/opt/MVS/lib64"})


def _translate_error(operation: str, error: Exception) -> PointCloudError:
    return PointCloudError(f"{operation}: {error}")


def _load_settings(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PointCloudError(f"point-cloud export config must be a regular file: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise _translate_error("cannot load point-cloud export config", exc) from exc
    if not isinstance(document, dict) or set(document) != SETTING_KEYS:
        raise PointCloudError("point-cloud export config fields do not match schema 1.0")
    if document.get("schema_version") != "1.0":
        raise PointCloudError("point-cloud export config schema_version must be 1.0")
    if document.get("source_topic") != "/cloud_registered":
        raise PointCloudError("point-cloud source_topic must be /cloud_registered")
    for field in (
        "chunk_duration_ns",
        "max_input_points_per_message",
        "max_input_points_per_chunk",
        "max_output_points_per_chunk",
        "max_chunks",
        "max_duration_ns",
    ):
        value = document.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise PointCloudError(f"{field} must be a positive integer")
    for field in ("voxel_leaf_m", "max_abs_coordinate_m"):
        value = document.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise PointCloudError(f"{field} must be a finite positive number")
    if document["max_input_points_per_chunk"] < document[
        "max_input_points_per_message"
    ]:
        raise PointCloudError(
            "max_input_points_per_chunk must cover at least one maximum message"
        )
    return document


def _processor_path() -> Path:
    try:
        prefix = Path(get_package_prefix("inspection_pointcloud_tools"))
    except PackageNotFoundError as exc:
        raise PointCloudError(
            "inspection_pointcloud_tools is not installed; rebuild the workspace"
        ) from exc
    executable = (
        prefix
        / "lib"
        / "inspection_pointcloud_tools"
        / "inspection_pcd_processor"
    )
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise PointCloudError(f"point-cloud PCL worker is unavailable: {executable}")
    return executable


def _exporter_git_commit() -> str:
    """Resolve the workspace commit without depending on the caller's directory."""
    for parent in Path(__file__).resolve().parents:
        if not (parent / ".git").exists():
            continue
        try:
            completed = subprocess.run(
                ["git", "-C", str(parent), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return "unknown"
        value = completed.stdout.strip()
        return value if value else "unknown"
    return "unknown"


def _parse_worker_output(output: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    summary: dict[str, str] = {}
    chunks: list[dict[str, Any]] = []
    for line in output.splitlines():
        if line.startswith("chunk="):
            values = line.removeprefix("chunk=").split("\t")
            if len(values) != 15:
                raise PointCloudError("PCL worker returned an invalid chunk record")
            try:
                integers = [int(item) for item in values[:9]]
                bounds = [float(item) for item in values[9:]]
            except ValueError as exc:
                raise PointCloudError("PCL worker returned invalid chunk numbers") from exc
            chunks.append(
                {
                    "seq": integers[0],
                    "window_index": integers[1],
                    "header_stamp_ns": {"first": integers[2], "last": integers[3]},
                    "bag_stamp_ns": {"first": integers[4], "last": integers[5]},
                    "message_count": integers[6],
                    "input_point_count": integers[7],
                    "output_point_count": integers[8],
                    "bounds_m": {"min": bounds[:3], "max": bounds[3:]},
                }
            )
        elif "=" in line:
            key, value = line.split("=", 1)
            if key in summary:
                raise PointCloudError(f"PCL worker repeated summary field {key}")
            summary[key] = value
        elif line.strip():
            raise PointCloudError("PCL worker returned unexpected output")
    required = {
        "pcl_version",
        "message_count",
        "input_point_count",
        "first_header_stamp_ns",
        "last_header_stamp_ns",
        "first_bag_stamp_ns",
        "last_bag_stamp_ns",
    }
    if set(summary) != required or not chunks:
        raise PointCloudError("PCL worker summary is incomplete")
    if [chunk["seq"] for chunk in chunks] != list(range(len(chunks))):
        raise PointCloudError("PCL worker chunk sequence is not contiguous")
    return summary, chunks


def _run_worker(
    *,
    bag_dir: Path,
    storage_id: str,
    frame_id: str,
    output_directory: Path,
    settings: dict[str, Any],
    translation: tuple[float, ...],
    rotation: tuple[float, ...],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    command = [
        str(_processor_path()),
        str(bag_dir),
        storage_id,
        str(settings["source_topic"]),
        frame_id,
        str(output_directory),
        str(settings["chunk_duration_ns"]),
        str(settings["voxel_leaf_m"]),
        str(settings["max_input_points_per_message"]),
        str(settings["max_input_points_per_chunk"]),
        str(settings["max_output_points_per_chunk"]),
        str(settings["max_chunks"]),
        str(settings["max_duration_ns"]),
        str(settings["max_abs_coordinate_m"]),
        *(str(value) for value in translation),
        *(str(value) for value in rotation),
    ]
    environment = os.environ.copy()
    library_entries = [
        entry
        for entry in environment.get("LD_LIBRARY_PATH", "").split(":")
        if entry and entry.rstrip("/") not in MVS_LIBRARY_PATHS
    ]
    environment["LD_LIBRARY_PATH"] = ":".join(dict.fromkeys(library_entries))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except OSError as exc:
        raise _translate_error("cannot start point-cloud PCL worker", exc) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise PointCloudError(f"point-cloud PCL worker failed: {detail}")
    return _parse_worker_output(completed.stdout)


def _same_tree(left: Path, right: Path) -> bool:
    if not left.is_dir() or left.is_symlink() or right.is_symlink():
        return False
    if any(path.is_symlink() for path in left.rglob("*")):
        return False
    left_files = sorted(
        path.relative_to(left) for path in left.rglob("*") if path.is_file()
    )
    right_files = sorted(
        path.relative_to(right) for path in right.rglob("*") if path.is_file()
    )
    if left_files != right_files:
        return False
    return all(
        (left / relative).read_bytes() == (right / relative).read_bytes()
        for relative in left_files
    )


def export_pointcloud(
    run_dir: Path | str,
    alignment_path: Path | str,
    *,
    export_config_path: Optional[Path | str] = None,
    allow_synthetic_alignment: bool = False,
    allow_non_mcap_bag: bool = False,
) -> Path:
    """Generate an atomic PCD chunk set and deterministic manifest."""
    unresolved_source = Path(run_dir).expanduser()
    if unresolved_source.is_symlink():
        raise PointCloudError("run directory must not be a symbolic link")
    source = unresolved_source.resolve()
    if not source.is_dir():
        raise PointCloudError(f"run directory does not exist: {source}")
    unresolved_alignment = Path(alignment_path).expanduser()
    if unresolved_alignment.is_symlink():
        raise PointCloudError("scene alignment must not be a symbolic link")
    alignment_source = unresolved_alignment.resolve()
    if export_config_path is None:
        export_config_source = (
            Path(get_package_share_directory("inspection_pipeline"))
            / "config/pointcloud_export.yaml"
        ).resolve()
    else:
        unresolved_config = Path(export_config_path).expanduser()
        if unresolved_config.is_symlink():
            raise PointCloudError("point-cloud export config must not be a symbolic link")
        export_config_source = unresolved_config.resolve()
    settings = _load_settings(export_config_source)
    try:
        operational, bringup, localization, _health, _base_to_body = _load_run_contract(
            source
        )
        alignment, _alignment_document = load_scene_alignment(
            alignment_source,
            allow_synthetic=allow_synthetic_alignment,
        )
    except TrajectoryError as exc:
        raise _translate_error("invalid run/alignment contract", exc) from exc
    if alignment.input_frame != localization.get("map_frame"):
        raise PointCloudError(
            "scene alignment input_frame must match localization_adapter.map_frame"
        )
    run_id = str(operational["run_id"])
    bag_reference = operational.get("bag")
    if not isinstance(bag_reference, dict):
        raise PointCloudError("operational manifest bag must be a mapping")
    storage_id = str(bag_reference.get("storage_id", ""))
    if storage_id != "mcap" and not allow_non_mcap_bag:
        raise PointCloudError("inspection point-cloud export requires MCAP storage")
    fast_lio_path = source / "config/fast_lio.yaml"
    if fast_lio_path.is_symlink() or not fast_lio_path.is_file():
        raise PointCloudError(
            "run is missing the immutable config/fast_lio.yaml snapshot"
        )
    contract_lock = _load_json(
        Path(get_package_share_directory("inspection_pipeline"))
        / "config/run-bundle.lock.json"
    )
    artifacts = source / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    if artifacts.is_symlink():
        raise PointCloudError("artifacts directory must not be a symbolic link")
    temporary = Path(tempfile.mkdtemp(prefix=".pointcloud.", dir=artifacts))
    try:
        chunks_dir = temporary / "chunks"
        chunks_dir.mkdir()
        summary, chunks = _run_worker(
            bag_dir=source / "bag" / run_id,
            storage_id=storage_id,
            frame_id=str(localization.get("input_global_frame", "")),
            output_directory=chunks_dir,
            settings=settings,
            translation=alignment.transform.translation,
            rotation=alignment.transform.rotation,
        )
        for chunk in chunks:
            path = chunks_dir / f"{chunk['seq']:06d}.pcd"
            chunk["path"] = f"{OUTPUT_RELATIVE_ROOT}/chunks/{path.name}"
            chunk["sha256"] = sha256(path)
        export_config_hash = sha256(export_config_source)
        manifest = {
            "schema_version": "1.0",
            "run_id": run_id,
            "coordinate_system": "scene_local_yup",
            "exporter": {
                "package": "inspection_pipeline",
                "version": "0.1.0",
                "git_commit": _exporter_git_commit(),
            },
            "source": {
                "bag_path": f"bag/{run_id}",
                "bag_storage_id": storage_id,
                "topic": settings["source_topic"],
                "type": SOURCE_TYPE,
                "fields": ["x", "y", "z", "intensity"],
                "frame_id": str(localization.get("input_global_frame", "")),
                "message_count": int(summary["message_count"]),
                "input_point_count": int(summary["input_point_count"]),
                "header_stamp_ns": {
                    "first": int(summary["first_header_stamp_ns"]),
                    "last": int(summary["last_header_stamp_ns"]),
                },
                "bag_stamp_ns": {
                    "first": int(summary["first_bag_stamp_ns"]),
                    "last": int(summary["last_bag_stamp_ns"]),
                },
            },
            "processor": {
                "backend": "pcl::VoxelGrid+PCDWriter",
                "pcl_version": summary["pcl_version"],
                "writer_format": "pcd-0.7-binary-xyzi",
                "selection_policy": "all_registered_scans_by_header_time_window",
                "chunk_duration_ns": settings["chunk_duration_ns"],
                "voxel_leaf_m": settings["voxel_leaf_m"],
                "limits": {
                    key: settings[key]
                    for key in (
                        "max_input_points_per_message",
                        "max_input_points_per_chunk",
                        "max_output_points_per_chunk",
                        "max_chunks",
                        "max_duration_ns",
                        "max_abs_coordinate_m",
                    )
                },
                "config": {
                    "path": "config/pointcloud_export.yaml",
                    "sha256": export_config_hash,
                },
            },
            "map_frame": str(localization.get("map_frame", "")),
            "frame_alias": {
                "parent_frame": str(localization.get("map_frame", "")),
                "child_frame": str(localization.get("input_global_frame", "")),
                "identity": True,
            },
            "extrinsics_verified": bringup.get("extrinsics_verified") is True,
            "system_config_sha256": sha256(source / "config/system.yaml"),
            "fast_lio_config": {
                "source_name": str(bringup.get("fast_lio_config_file", "")),
                "path": "config/fast_lio.yaml",
                "sha256": sha256(fast_lio_path),
            },
            "alignment": {
                "alignment_id": alignment.alignment_id,
                "source_kind": alignment.source_kind,
                "verified": True,
                "sha256": alignment.source_sha256,
                "transform": {
                    "translation_m": list(alignment.transform.translation),
                    "rotation_xyzw": list(alignment.transform.rotation),
                },
            },
            "contract_authority": contract_lock["authority"],
            "chunks": chunks,
        }
        (temporary / "manifest.json").write_bytes(_json_bytes(manifest))
        validate_pointcloud_manifest(
            manifest,
            pointcloud_root=temporary,
            run_id=run_id,
            alignment_id=alignment.alignment_id,
            alignment_sha256=alignment.source_sha256,
            alignment_translation=alignment.transform.translation,
            alignment_rotation=alignment.transform.rotation,
            system_sha256=sha256(source / "config/system.yaml"),
            fast_lio_sha256=sha256(fast_lio_path),
            export_config_sha256=export_config_hash,
            storage_id=storage_id,
            contract_authority=contract_lock["authority"],
        )
        try:
            _write_atomic(
                source / "config/scene_alignment.yaml",
                alignment_source.read_bytes(),
                replace=False,
            )
            _write_atomic(
                source / "config/pointcloud_export.yaml",
                export_config_source.read_bytes(),
                replace=False,
            )
        except TrajectoryError as exc:
            raise _translate_error("cannot freeze point-cloud export config", exc) from exc
        target = artifacts / "pointcloud"
        if target.exists():
            if _same_tree(target, temporary):
                shutil.rmtree(temporary)
                return target / "manifest.json"
            raise PointCloudError(
                f"point-cloud output already exists with different content: {target}"
            )
        os.replace(temporary, target)
        parent_descriptor = os.open(artifacts, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        return target / "manifest.json"
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("alignment", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--allow-synthetic-alignment", action="store_true")
    parser.add_argument("--allow-non-mcap-bag", action="store_true")
    args = parser.parse_args(argv)
    output = export_pointcloud(
        args.run_dir,
        args.alignment,
        export_config_path=args.config,
        allow_synthetic_alignment=args.allow_synthetic_alignment,
        allow_non_mcap_bag=args.allow_non_mcap_bag,
    )
    print(output)
    return 0
