"""Validated rosbag2 recording configuration and storage health helpers."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class RecordingConfigError(ValueError):
    """The recording policy is unsafe or cannot be loaded."""


@dataclass(frozen=True)
class RecordingSettings:
    """One immutable recording and disk-retention policy."""

    profile_id: str
    source_path: Path
    storage_id: str
    storage_preset_profile: str
    storage_config_path: Path
    max_bagfile_size_bytes: int
    max_bagfile_duration_seconds: int
    max_cache_size_bytes: int
    max_run_size_bytes: int
    min_start_free_space_bytes: int
    min_runtime_free_space_bytes: int
    monitor_period_seconds: float
    stop_timeout_seconds: float
    topics: tuple[str, ...]
    required_topics: tuple[str, ...]
    imu_qos_depth: int = 10

    def recorder_command(
        self,
        bag_path: Path,
        *,
        storage_config_path: Path | None = None,
    ) -> list[str]:
        """Build the explicit ros2 bag command for this policy."""
        return [
            "ros2",
            "bag",
            "record",
            "-o",
            str(bag_path),
            "-s",
            self.storage_id,
            "--max-bag-size",
            str(self.max_bagfile_size_bytes),
            "--max-bag-duration",
            str(self.max_bagfile_duration_seconds),
            "--max-cache-size",
            str(self.max_cache_size_bytes),
            *([
                "--qos-profile-overrides-path",
                str((storage_config_path or self.storage_config_path).parent / "recording_qos.yaml"),
            ] if self.imu_qos_depth != 10 and "/livox/imu" in self.topics else []),
            "--storage-preset-profile",
            self.storage_preset_profile,
            "--storage-config-file",
            str(storage_config_path or self.storage_config_path),
            *self.topics,
        ]


@dataclass(frozen=True)
class StorageHealth:
    """Current capacity of the run filesystem and active run tree."""

    free_bytes: int
    run_size_bytes: int


_FATAL_LOG_PATTERNS = {
    "recorder_cache_message_loss": (
        "messages will be lost",
        "cache buffers lost messages per topic",
        "dropping message!",
        "cache buffers were unflushed",
    ),
    "recorder_storage_io_error": (
        "no space left on device",
        "input/output error",
        "read-only file system",
        "failed to write",
        "write failed",
        "failed to flush",
    ),
    "recorder_writer_exception": (
        "writer exception",
        "failed to close",
        "storage exception",
    ),
}


# Exactly two public recording profile IDs exist (scout_mini_ws#3).
PROFILE_IDS = ("acceptance_raw", "routine")

_ALLOWED_KEYS = {
    "schema_version",
    "profile_id",
    "profiles",
    "storage_id",
    "storage_config_file",
    "max_bagfile_size_bytes",
    "max_bagfile_duration_seconds",
    "max_cache_size_bytes",
    "imu_qos_depth",
    "max_run_size_bytes",
    "min_start_free_space_bytes",
    "min_runtime_free_space_bytes",
    "monitor_period_seconds",
    "stop_timeout_seconds",
}
_ALLOWED_PROFILE_KEYS = {
    "storage_preset_profile",
    "topics",
    "required_topics",
}
_ALLOWED_MCAP_KEYS = {
    "noChunking",
    "noChunkCRC",
    "noSummaryCRC",
    "noMessageIndex",
    "noSummary",
    "compression",
    "compressionLevel",
    "chunkSize",
    "forceCompression",
}


def sha256_file(path: Path) -> str:
    """Hash a regular file without loading large content into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_int(document: dict[str, Any], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RecordingConfigError(f"{key} must be a positive integer")
    return value


def _positive_number(document: dict[str, Any], key: str) -> float:
    value = document.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise RecordingConfigError(f"{key} must be a positive number")
    return float(value)


def _topics(document: dict[str, Any], key: str) -> tuple[str, ...]:
    values = document.get(key)
    if not isinstance(values, list) or not values:
        raise RecordingConfigError(f"{key} must be a non-empty list")
    if any(
        not isinstance(value, str)
        or not value.startswith("/")
        or value == "/"
        or "//" in value
        for value in values
    ):
        raise RecordingConfigError(f"{key} contains an invalid absolute ROS topic")
    if len(set(values)) != len(values):
        raise RecordingConfigError(f"{key} must not contain duplicates")
    return tuple(values)


def load_recording_settings(path: Path | str) -> RecordingSettings:
    """Load and strictly validate a recording policy and MCAP writer config."""
    unresolved_source = Path(path).expanduser()
    if unresolved_source.is_symlink():
        raise RecordingConfigError(
            f"recording config must not be a symbolic link: {unresolved_source}"
        )
    source = unresolved_source.resolve()
    if not source.is_file():
        raise RecordingConfigError(f"recording config must be a regular file: {source}")
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RecordingConfigError(f"cannot load recording config: {exc}") from exc
    if not isinstance(document, dict):
        raise RecordingConfigError("recording config root must be a mapping")
    if document.get("schema_version") != "1.0":
        raise RecordingConfigError("schema_version must be '1.0'")
    if document.get("storage_id") != "mcap":
        raise RecordingConfigError("storage_id must be 'mcap'")
    # The migration check comes first: a legacy flat config is missing profile_id
    # by definition and must get the actionable message, not a generic
    # unknown-keys error.
    profile_id = document.get("profile_id")
    if profile_id is None:
        raise RecordingConfigError(
            "recording config predates dual-profile support: set 'profile_id' to "
            "'acceptance_raw' (formal acceptance run: every recorded topic is "
            "required) or 'routine' (bounded daily policy) in the recording config; "
            "a profile is never inferred from the config filename"
        )
    unknown = set(document) - _ALLOWED_KEYS
    if unknown:
        raise RecordingConfigError(
            f"recording config has unknown keys: {', '.join(sorted(unknown))}"
        )
    if profile_id not in PROFILE_IDS:
        raise RecordingConfigError(
            f"profile_id must be one of {', '.join(PROFILE_IDS)}; got {profile_id!r}"
        )
    profiles = document.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != set(PROFILE_IDS):
        raise RecordingConfigError(
            "profiles must define exactly two profiles: " + ", ".join(PROFILE_IDS)
        )
    for name, section in profiles.items():
        if not isinstance(section, dict):
            raise RecordingConfigError(f"profile {name} must be a mapping")
        unknown_profile_keys = set(section) - _ALLOWED_PROFILE_KEYS
        if unknown_profile_keys:
            raise RecordingConfigError(
                f"profile {name} has unknown keys: "
                + ", ".join(sorted(unknown_profile_keys))
            )
        if section.get("storage_preset_profile") not in {"none", "zstd_fast", "zstd_small"}:
            raise RecordingConfigError(
                f"profile {name} storage_preset_profile must be none, zstd_fast, "
                "or zstd_small"
            )
    active = profiles[profile_id]
    preset = active["storage_preset_profile"]
    storage_config_name = document.get("storage_config_file")
    if (
        not isinstance(storage_config_name, str)
        or Path(storage_config_name).name != storage_config_name
        or Path(storage_config_name).suffix not in {".yaml", ".yml"}
    ):
        raise RecordingConfigError("storage_config_file must be a YAML filename")
    unresolved_storage_config = source.parent / storage_config_name
    if unresolved_storage_config.is_symlink():
        raise RecordingConfigError(
            f"MCAP storage config must not be a symbolic link: {unresolved_storage_config}"
        )
    storage_config = unresolved_storage_config.resolve()
    if not storage_config.is_file():
        raise RecordingConfigError(
            f"MCAP storage config must be a regular file: {storage_config}"
        )
    try:
        storage_document = yaml.safe_load(storage_config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RecordingConfigError(f"cannot load MCAP storage config: {exc}") from exc
    if not isinstance(storage_document, dict):
        raise RecordingConfigError("MCAP storage config root must be a mapping")
    unknown_storage_keys = set(storage_document) - _ALLOWED_MCAP_KEYS
    if unknown_storage_keys:
        raise RecordingConfigError(
            "MCAP storage config has unknown keys: "
            + ", ".join(sorted(unknown_storage_keys))
        )
    if storage_document.get("compression") != "Zstd":
        raise RecordingConfigError("MCAP storage config compression must be Zstd")
    if storage_document.get("compressionLevel") != "Fastest":
        raise RecordingConfigError(
            "MCAP storage config compressionLevel must be Fastest"
        )
    if storage_document.get("noChunking") is not False:
        raise RecordingConfigError("MCAP chunking must remain enabled")
    if storage_document.get("noChunkCRC") is not False:
        raise RecordingConfigError("MCAP chunk CRC must remain enabled")
    for key in ("noSummaryCRC", "noMessageIndex", "noSummary", "forceCompression"):
        if storage_document.get(key) is not False:
            raise RecordingConfigError(f"MCAP storage config {key} must be false")
    chunk_size = storage_document.get("chunkSize")
    if (
        not isinstance(chunk_size, int)
        or isinstance(chunk_size, bool)
        or not 64 * 1024 <= chunk_size <= 16 * 1024 * 1024
    ):
        raise RecordingConfigError(
            "MCAP storage config chunkSize must be between 64 KiB and 16 MiB"
        )

    topics = _topics(active, "topics")
    required_topics = _topics(active, "required_topics")
    missing = set(required_topics) - set(topics)
    if missing:
        raise RecordingConfigError(
            f"required_topics are absent from topics: {', '.join(sorted(missing))}"
        )
    if profile_id == "acceptance_raw" and set(topics) != set(required_topics):
        raise RecordingConfigError(
            "acceptance_raw treats every recorded topic as required: topics must "
            "equal required_topics so no acceptance topic can be demoted to "
            f"optional (offending optional topics: "
            f"{', '.join(sorted(set(topics) - set(required_topics)))})"
        )
    if profile_id == "acceptance_raw" and not required_topics:
        raise RecordingConfigError("acceptance_raw requires a non-empty topic set")
    max_bagfile_size = _positive_int(document, "max_bagfile_size_bytes")
    max_run_size = _positive_int(document, "max_run_size_bytes")
    start_free = _positive_int(document, "min_start_free_space_bytes")
    runtime_free = _positive_int(document, "min_runtime_free_space_bytes")
    if max_run_size < max_bagfile_size:
        raise RecordingConfigError(
            "max_run_size_bytes must be at least max_bagfile_size_bytes"
        )
    if start_free < runtime_free:
        raise RecordingConfigError(
            "min_start_free_space_bytes must be at least min_runtime_free_space_bytes"
        )
    imu_qos_depth = _positive_int(
        {"imu_qos_depth": document.get("imu_qos_depth", 10)}, "imu_qos_depth"
    )
    if imu_qos_depth > 65536:
        raise RecordingConfigError("imu_qos_depth must not exceed 65536")
    return RecordingSettings(
        profile_id=str(profile_id),
        source_path=source,
        storage_id="mcap",
        storage_preset_profile=str(preset),
        storage_config_path=storage_config,
        max_bagfile_size_bytes=max_bagfile_size,
        max_bagfile_duration_seconds=_positive_int(
            document, "max_bagfile_duration_seconds"
        ),
        max_cache_size_bytes=_positive_int(document, "max_cache_size_bytes"),
        max_run_size_bytes=max_run_size,
        min_start_free_space_bytes=start_free,
        min_runtime_free_space_bytes=runtime_free,
        monitor_period_seconds=_positive_number(document, "monitor_period_seconds"),
        stop_timeout_seconds=_positive_number(document, "stop_timeout_seconds"),
        topics=topics,
        required_topics=required_topics,
        imu_qos_depth=imu_qos_depth,
    )


def snapshot_recording_settings(
    settings: RecordingSettings,
    destination: Path,
) -> dict[str, Any]:
    """Freeze both recording policy files and return their manifest provenance."""
    destination.mkdir(parents=True, exist_ok=True)
    recording_snapshot = destination / "recording.yaml"
    storage_snapshot = destination / settings.storage_config_path.name
    shutil.copy2(settings.source_path, recording_snapshot)
    shutil.copy2(
        settings.storage_config_path,
        storage_snapshot,
    )
    if settings.imu_qos_depth != 10 and "/livox/imu" in settings.topics:
        (destination / "recording_qos.yaml").write_text(yaml.safe_dump({
            "/livox/imu": {"history": "keep_last", "depth": settings.imu_qos_depth,
                           "reliability": "reliable"},
        }), encoding="utf-8")
    return {
        "profile_id": settings.profile_id,
        "config_path": "config/recording.yaml",
        "config_sha256": sha256_file(recording_snapshot),
        "storage_config_path": f"config/{storage_snapshot.name}",
        "storage_config_sha256": sha256_file(storage_snapshot),
        "recorder_exit_code": None,
        "log_path": "recording/rosbag.log",
    }


def directory_size_bytes(root: Path) -> int:
    """Return the size of regular files in a non-symlink run tree."""
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise OSError(f"symbolic link found in run directory: {path}")
        if path.is_file():
            total += path.stat().st_size
    return total


def storage_health(filesystem_path: Path, run_dir: Path) -> StorageHealth:
    """Measure filesystem free space and bytes already consumed by this run."""
    return StorageHealth(
        free_bytes=shutil.disk_usage(filesystem_path).free,
        run_size_bytes=directory_size_bytes(run_dir),
    )


def preflight_storage(output_root: Path) -> StorageHealth:
    """Prove that the recording root is writable and supports durable metadata."""
    if output_root.is_symlink():
        raise OSError(f"recording output must not be a symbolic link: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    if output_root.is_symlink() or not output_root.is_dir():
        raise OSError(f"recording output must be a real directory: {output_root}")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".recording-preflight.",
            dir=output_root,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(b"SCOUT recording preflight\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.unlink()
        temporary_path = None
        descriptor = os.open(output_root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return storage_health(output_root, output_root / ".no-active-run")


def scan_recorder_log(path: Path, offset: int) -> tuple[int, str | None]:
    """Scan newly appended recorder diagnostics for fail-closed conditions."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return 0, None
    if offset < 0 or offset > size:
        offset = 0
    with path.open("rb") as stream:
        stream.seek(max(0, offset - 256))
        content = stream.read().decode("utf-8", errors="replace").lower()
        next_offset = stream.tell()
    for reason, patterns in _FATAL_LOG_PATTERNS.items():
        if any(pattern in content for pattern in patterns):
            return next_offset, reason
    return next_offset, None
