"""Validate a finalized SCOUT run before it is copied to removable media."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import jsonschema
import rosbag2_py
from ament_index_python.packages import get_package_share_directory

from inspection_pipeline.bag_health import BagHealthError, inspect_bag
from inspection_pipeline.bundle_export import (
    BundleExportError,
    _assert_plain_tree,
    _build_semantic_manifest,
    _load_json,
)
from inspection_pipeline.recording import (
    RecordingConfigError,
    load_recording_settings,
    sha256_file,
)
from inspection_pipeline.run_manifest import write_manifest
from inspection_pipeline.station_attempts import StationEvidenceError
from inspection_pipeline.station_attempts_export import validate_station_attempts_artifact


class RunValidationError(ValueError):
    """A finalized run is not complete or internally consistent."""


def _validate_operational_schema(operational: dict[str, Any]) -> None:
    source_schema = Path(__file__).resolve().parents[3] / "docs/manifest.schema.json"
    schema_path = source_schema
    if not schema_path.is_file():
        schema_path = (
            Path(get_package_share_directory("inspection_pipeline"))
            / "schema/manifest.schema.json"
        )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        ).validate(operational)
    except (OSError, json.JSONDecodeError, jsonschema.SchemaError) as exc:
        raise RunValidationError(f"cannot load operational manifest schema: {exc}") from exc
    except jsonschema.ValidationError as exc:
        location = ".".join(str(item) for item in exc.absolute_path) or "root"
        raise RunValidationError(
            f"operational manifest schema failed at {location}: {exc.message}"
        ) from exc


def _recording_reference(
    operational: dict[str, Any],
    run_dir: Path,
) -> tuple[dict[str, Any], Any]:
    reference = operational.get("recording")
    expected_keys = {
        "profile_id",
        "config_path",
        "config_sha256",
        "storage_config_path",
        "storage_config_sha256",
        "recorder_exit_code",
        "log_path",
    }
    if not isinstance(reference, dict) or set(reference) != expected_keys:
        raise RunValidationError("operational manifest recording provenance is invalid")
    if reference.get("config_path") != "config/recording.yaml":
        raise RunValidationError("recording config path is invalid")
    config_path = run_dir / "config/recording.yaml"
    if not config_path.is_file() or config_path.is_symlink():
        raise RunValidationError("frozen recording config is missing")
    if reference.get("config_sha256") != sha256_file(config_path):
        raise RunValidationError("recording config SHA-256 mismatch")
    try:
        settings = load_recording_settings(config_path)
    except RecordingConfigError as exc:
        raise RunValidationError(f"invalid frozen recording config: {exc}") from exc
    if reference.get("profile_id") != settings.profile_id:
        raise RunValidationError(
            "manifest recording profile_id does not match the frozen config"
        )
    expected_storage_path = f"config/{settings.storage_config_path.name}"
    if reference.get("storage_config_path") != expected_storage_path:
        raise RunValidationError("MCAP storage config path mismatch")
    if reference.get("storage_config_sha256") != sha256_file(
        settings.storage_config_path
    ):
        raise RunValidationError("MCAP storage config SHA-256 mismatch")
    if reference.get("log_path") != "recording/rosbag.log":
        raise RunValidationError("rosbag log path is invalid")
    log_path = run_dir / "recording/rosbag.log"
    if not log_path.is_file() or log_path.is_symlink():
        raise RunValidationError("rosbag diagnostic log is missing")
    exit_code = reference.get("recorder_exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise RunValidationError("finalized run has no recorder exit code")
    if exit_code != 0:
        raise RunValidationError(f"rosbag recorder exited abnormally: {exit_code}")
    return reference, settings


def _scan_rosbag_messages(bag_dir: Path, storage_id: str) -> tuple[int, list[str]]:
    reader = rosbag2_py.SequentialReader()
    try:
        reader.open(
            rosbag2_py.StorageOptions(uri=str(bag_dir), storage_id=storage_id),
            rosbag2_py.ConverterOptions(
                input_serialization_format="",
                output_serialization_format="",
            ),
        )
        topics = sorted(item.name for item in reader.get_all_topics_and_types())
        count = 0
        while reader.has_next():
            reader.read_next()
            count += 1
    except RuntimeError as exc:
        raise RunValidationError(f"MCAP structural scan failed: {exc}") from exc
    return count, topics


def _doctor_mcap_files(
    bag_dir: Path,
    executable: Path | str | None,
) -> tuple[str, list[str]]:
    resolved = str(executable) if executable is not None else shutil.which("mcap")
    if not resolved:
        raise RunValidationError(
            "official mcap CLI is unavailable; install pinned mcap-cli v0.3.0"
        )
    try:
        version_result = subprocess.run(
            [resolved, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunValidationError(f"cannot run mcap CLI: {exc}") from exc
    version = version_result.stdout.strip() or version_result.stderr.strip()
    if "0.3.0" not in version:
        raise RunValidationError(f"mcap CLI version is not pinned v0.3.0: {version}")
    files = sorted(path for path in bag_dir.rglob("*.mcap") if path.is_file())
    validated: list[str] = []
    for path in files:
        try:
            result = subprocess.run(
                [resolved, "doctor", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise RunValidationError(f"cannot run mcap doctor: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr.strip() or result.stdout.strip())[-1000:]
            raise RunValidationError(
                f"mcap doctor failed for {path.name}: {detail}"
            )
        validated.append(path.name)
    if not validated:
        raise RunValidationError("mcap doctor found no MCAP files")
    return version, validated


def _hash_inventory(run_dir: Path) -> list[dict[str, Any]]:
    included_roots = (
        "manifest.json",
        "config",
        "replay",
        "recording",
        "bag",
        "artifacts",
        "acceptance",
        "alignment",
        "scene",
    )
    files: list[Path] = []
    for name in included_roots:
        path = run_dir / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file())
    return [
        {
            "path": path.relative_to(run_dir).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(files, key=lambda item: item.relative_to(run_dir).as_posix())
    ]


def validate_run_directory(
    run_dir: Path | str,
    *,
    full_mcap_scan: bool = True,
    mcap_doctor_executable: Path | str | None = None,
    recording_only: bool = False,
    require_station_attempts: bool = False,
) -> dict[str, Any]:
    """Validate all run contracts and return a deterministic evidence report."""
    unresolved = Path(run_dir).expanduser()
    if unresolved.is_symlink():
        raise RunValidationError("run directory must not be a symbolic link")
    source = unresolved.resolve()
    try:
        _assert_plain_tree(source)
        operational = _load_json(source / "manifest.json")
        _validate_operational_schema(operational)
        if require_station_attempts and operational.get("station_attempts") is None:
            raise RunValidationError(
                "station evidence required; run inspection_station_attempts_export first"
            )
        _reference, settings = _recording_reference(operational, source)
        if recording_only:
            run_id = operational.get("run_id")
            status = operational.get("status")
            if status not in {"completed", "completed_with_exceptions"}:
                raise RunValidationError(
                    f"recording-only validation requires a completed run: {status}"
                )
            bag_reference = operational.get("bag", {})
            expected_bag_path = str(source / "bag" / str(run_id))
            if bag_reference.get("storage_id") != settings.storage_id:
                raise RunValidationError("operational manifest bag.storage_id mismatch")
            if bag_reference.get("path") != expected_bag_path:
                raise RunValidationError("operational manifest bag.path mismatch")
            trajectory_validated = False
            try:
                validate_station_attempts_artifact(source)
            except StationEvidenceError as exc:
                raise RunValidationError(f"invalid station evidence: {exc}") from exc
        else:
            trajectory = _load_json(source / "replay/trajectory.json")
            alignment_id = trajectory.get("alignment_id")
            if not isinstance(alignment_id, str) or not alignment_id:
                raise RunValidationError("trajectory alignment_id is missing")
            semantic = _build_semantic_manifest(
                operational,
                run_dir=source,
                scene_id="run-validation",
                alignment_id=alignment_id,
                name=None,
            )
            run_id = semantic["run_id"]
            status = semantic["status"]
            bag_reference = operational.get("bag", {})
            trajectory_validated = True
        summary = inspect_bag(
            source / "bag" / str(run_id),
            storage_id=settings.storage_id,
            required_topics=settings.required_topics,
        )
    except (BagHealthError, BundleExportError, OSError) as exc:
        if isinstance(exc, RunValidationError):
            raise
        raise RunValidationError(str(exc)) from exc

    expected_summary = summary.manifest_fields()
    for key, value in expected_summary.items():
        if bag_reference.get(key) != value:
            raise RunValidationError(f"operational manifest bag.{key} mismatch")
    doctor_version, doctored_files = _doctor_mcap_files(
        source / "bag" / str(run_id),
        mcap_doctor_executable,
    )
    scanned_messages: Optional[int] = None
    scanned_topics: Optional[list[str]] = None
    if full_mcap_scan:
        scanned_messages, scanned_topics = _scan_rosbag_messages(
            source / "bag" / str(run_id),
            settings.storage_id,
        )
        if scanned_messages != summary.message_count:
            raise RunValidationError(
                "MCAP scanned message count does not match rosbag metadata"
            )
        if scanned_topics != list(summary.topics):
            raise RunValidationError("MCAP scanned topics do not match rosbag metadata")

    inventory = _hash_inventory(source)
    duration_seconds = summary.duration_nanoseconds / 1_000_000_000
    topic_counts = dict(summary.topic_counts)
    return {
        "schema_version": "1.0",
        "valid": True,
        "validated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "status": status,
        "run_mode": operational.get("run_mode", "legacy_unspecified"),
        "checks": {
            "manifest": True,
            "recording_config": True,
            "mcap_metadata": True,
            "mcap_doctor": True,
            "mcap_full_scan": full_mcap_scan,
            "route": operational.get("fixed_route") is not None,
            "station_attempts": operational.get("station_attempts") is not None,
            "trajectory": trajectory_validated,
            "pointcloud": (source / "artifacts/pointcloud").is_dir(),
            "hash_integrity": True,
        },
        "bag": {
            **expected_summary,
            "duration_seconds": duration_seconds,
            "topics": list(summary.topics),
            "topic_counts": topic_counts,
            "topic_average_hz": {
                topic: None if duration_seconds == 0 else count / duration_seconds
                for topic, count in summary.topic_counts
            },
            "scanned_message_count": scanned_messages,
            "scanned_topics": scanned_topics,
            "doctor_version": doctor_version,
            "doctored_files": doctored_files,
        },
        "files": inventory,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="skip the slower ROS sequential scan (mcap doctor still runs)",
    )
    parser.add_argument(
        "--recording-only",
        action="store_true",
        help="validate a completed commissioning recording without delivery artifacts",
    )
    parser.add_argument("--output", type=Path, help="optionally write the JSON report")
    parser.add_argument(
        "--require-station-attempts", action="store_true",
        help="require indexed station evidence instead of accepting an unindexed legacy run",
    )
    args = parser.parse_args(argv)
    try:
        report = validate_run_directory(
            args.run_dir,
            full_mcap_scan=not args.metadata_only,
            recording_only=args.recording_only,
            require_station_attempts=args.require_station_attempts,
        )
    except RunValidationError as exc:
        report = {"schema_version": "1.0", "valid": False, "error": str(exc)}
    if args.output is not None:
        write_manifest(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
