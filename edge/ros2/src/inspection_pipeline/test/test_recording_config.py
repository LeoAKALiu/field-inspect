"""Dual recording-profile contract tests (acceptance_raw / routine).

对应 scout_mini_ws#3 的双录制 profile 切片：
- 恰好两个公共 profile_id：acceptance_raw 与 routine；
- profile 选择必须显式（recording.yaml 的 profile_id），缺失/未知/歧义一律 fail closed，
  旧版扁平配置必须得到可操作的迁移错误；
- acceptance_raw：所有录制话题都是必需话题（不允许任何话题降级为可选），
  原始话题保真——无损 Zstd 分块压缩保持启用（raw ≠ 未压缩字节）；
- routine：显式的有界压缩/保留策略，允许经审查缩减可选话题，但必须覆盖 required_topics；
- manifest.recording.profile_id 进入 docs/manifest.schema.json 并被快照链保留。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from inspection_pipeline.recording import (
    RecordingConfigError,
    load_recording_settings,
    snapshot_recording_settings,
)

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_SCHEMA_PATH = REPO_ROOT / "docs" / "manifest.schema.json"

STORAGE_CONFIG_NAME = "mcap_writer_options.yaml"
STORAGE_CONFIG_TEXT = (
    "noChunking: false\n"
    "noChunkCRC: false\n"
    "noSummaryCRC: false\n"
    "noMessageIndex: false\n"
    "noSummary: false\n"
    "compression: Zstd\n"
    "compressionLevel: Fastest\n"
    "chunkSize: 1048576\n"
    "forceCompression: false\n"
)

RAW_TOPICS = [
    "/livox/lidar",
    "/livox/imu",
    "/camera/image_raw",
    "/camera/camera_info",
    "/odom",
    "/scout_status",
    "/Odometry",
    "/Laser_map",
    "/cloud_registered",
    "/cloud_registered_body",
    "/detections",
    "/tf",
    "/tf_static",
    "/cmd_vel_safe",
    "/safety/obstacle_clear",
    "/inspection/status",
    "/route/status",
    "/diagnostics",
]

REQUIRED_TOPICS = [
    "/livox/lidar",
    "/livox/imu",
    "/camera/image_raw",
    "/camera/camera_info",
    "/scout_status",
]

PROFILE_IDS = ("acceptance_raw", "routine")


def _profile_section(
    *,
    topics: list[str] | None = None,
    required_topics: list[str] | None = None,
    preset: str = "zstd_fast",
) -> dict:
    return {
        "storage_preset_profile": preset,
        "topics": RAW_TOPICS if topics is None else topics,
        "required_topics": REQUIRED_TOPICS if required_topics is None else required_topics,
    }


def _document(profile_id: str | None = "routine", **overrides) -> dict:
    document = {
        "schema_version": "1.0",
        "storage_id": "mcap",
        "storage_config_file": STORAGE_CONFIG_NAME,
        "max_bagfile_size_bytes": 2 * 1024**3,
        "max_bagfile_duration_seconds": 300,
        "max_cache_size_bytes": 64 * 1024**2,
        "max_run_size_bytes": 100 * 1024**3,
        "min_start_free_space_bytes": 30 * 1024**3,
        "min_runtime_free_space_bytes": 20 * 1024**3,
        "monitor_period_seconds": 1.0,
        "stop_timeout_seconds": 30.0,
        "profiles": {
            "acceptance_raw": _profile_section(
                topics=list(RAW_TOPICS), required_topics=list(RAW_TOPICS)
            ),
            "routine": _profile_section(),
        },
        **overrides,
    }
    if profile_id is not None:
        document["profile_id"] = profile_id
    return document


def _write_config(tmp_path: Path, document: dict) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "recording.yaml"
    config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    (config_path.parent / STORAGE_CONFIG_NAME).write_text(STORAGE_CONFIG_TEXT, encoding="utf-8")
    return config_path


def test_repository_config_declares_both_profiles() -> None:
    """仓库自带的 recording.yaml 必须声明恰好两个 profile 并显式选择其一。"""
    document = yaml.safe_load((CONFIG_DIR / "recording.yaml").read_text(encoding="utf-8"))
    assert document["profile_id"] in PROFILE_IDS
    assert set(document["profiles"]) == set(PROFILE_IDS)


def test_imu_queue_override_is_frozen_with_the_recording_policy(tmp_path) -> None:
    settings = load_recording_settings(_write_config(tmp_path, _document(imu_qos_depth=4096)))
    frozen = tmp_path / "run/config"
    snapshot_recording_settings(settings, frozen)
    command = settings.recorder_command(
        tmp_path / "run/bag", storage_config_path=frozen / STORAGE_CONFIG_NAME
    )
    qos_path = Path(command[command.index("--qos-profile-overrides-path") + 1])
    assert qos_path.parent == frozen
    assert yaml.safe_load(qos_path.read_text()) == {
        "/livox/imu": {"history": "keep_last", "depth": 4096, "reliability": "reliable"}
    }
    assert yaml.safe_load((frozen / "recording.yaml").read_text())["imu_qos_depth"] == 4096
    for invalid in (0, True, 65537):
        with pytest.raises(RecordingConfigError, match="imu_qos_depth"):
            load_recording_settings(_write_config(tmp_path, _document(imu_qos_depth=invalid)))


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_both_profile_ids_load_and_build_intended_command(tmp_path, profile_id: str) -> None:
    """两个 profile 都能加载并构建出预期的 ros2 bag record 命令。"""
    settings = load_recording_settings(_write_config(tmp_path, _document(profile_id)))
    assert settings.profile_id == profile_id
    command = settings.recorder_command(Path("/tmp/run/bag"))
    assert command[:2] == ["ros2", "bag"]
    assert command[command.index("--storage-preset-profile") + 1] == "zstd_fast"
    assert command[command.index("-s") + 1] == "mcap"
    recorded = command[command.index("--storage-config-file") + 2 :]
    assert recorded == list(settings.topics)


def test_acceptance_raw_treats_every_recorded_topic_as_required(tmp_path) -> None:
    """acceptance_raw：topics 必须与 required_topics 完全一致（无任何可选话题）。"""
    document = _document("acceptance_raw")
    settings = load_recording_settings(_write_config(tmp_path, document))
    assert settings.profile_id == "acceptance_raw"
    assert set(settings.topics) == set(settings.required_topics)

    reduced = _document(
        "acceptance_raw",
        profiles={
            "acceptance_raw": _profile_section(
                topics=list(RAW_TOPICS),
                required_topics=[topic for topic in RAW_TOPICS if topic != "/detections"],
            ),
            "routine": _profile_section(),
        },
    )
    with pytest.raises(RecordingConfigError, match="acceptance_raw"):
        load_recording_settings(_write_config(tmp_path, reduced))


def test_routine_has_explicit_bounded_policy_and_required_subset(tmp_path) -> None:
    """routine：显式有界压缩/保留策略；可选话题可缩减但不得低于 required_topics。"""
    settings = load_recording_settings(_write_config(tmp_path, _document("routine")))
    assert settings.profile_id == "routine"
    assert settings.storage_preset_profile == "zstd_fast"
    assert settings.max_bagfile_size_bytes == 2 * 1024**3
    assert settings.max_bagfile_duration_seconds == 300
    assert settings.max_run_size_bytes == 100 * 1024**3
    assert set(settings.required_topics).issubset(set(settings.topics))

    reduced_topics = [
        topic for topic in RAW_TOPICS if topic not in {"/Laser_map", "/detections"}
    ]
    document = _document(
        "routine",
        profiles={
            "acceptance_raw": _profile_section(
                topics=list(RAW_TOPICS), required_topics=list(RAW_TOPICS)
            ),
            "routine": _profile_section(topics=reduced_topics),
        },
    )
    reduced = load_recording_settings(_write_config(tmp_path, document))
    assert set(REQUIRED_TOPICS).issubset(set(reduced.topics))

    document["profiles"]["routine"] = _profile_section(
        topics=[topic for topic in reduced_topics if topic != "/scout_status"]
    )
    with pytest.raises(RecordingConfigError, match="required_topics"):
        load_recording_settings(_write_config(tmp_path, document))


def test_missing_profile_id_rejects_legacy_config_with_migration_hint(tmp_path) -> None:
    """旧版扁平配置（无 profile_id）必须得到可操作的迁移错误。"""
    legacy = {
        "schema_version": "1.0",
        "storage_id": "mcap",
        "storage_preset_profile": "zstd_fast",
        "storage_config_file": STORAGE_CONFIG_NAME,
        "max_bagfile_size_bytes": 2 * 1024**3,
        "max_bagfile_duration_seconds": 300,
        "max_cache_size_bytes": 64 * 1024**2,
        "max_run_size_bytes": 100 * 1024**3,
        "min_start_free_space_bytes": 30 * 1024**3,
        "min_runtime_free_space_bytes": 20 * 1024**3,
        "monitor_period_seconds": 1.0,
        "stop_timeout_seconds": 30.0,
        "topics": list(RAW_TOPICS),
        "required_topics": list(REQUIRED_TOPICS),
    }
    with pytest.raises(RecordingConfigError) as excinfo:
        load_recording_settings(_write_config(tmp_path, legacy))
    message = str(excinfo.value)
    assert "profile_id" in message
    assert "acceptance_raw" in message and "routine" in message


def test_unknown_profile_id_fails_closed(tmp_path) -> None:
    with pytest.raises(RecordingConfigError, match="profile_id"):
        load_recording_settings(_write_config(tmp_path, _document("raw")))


def test_extra_or_missing_profile_definitions_fail_closed(tmp_path) -> None:
    """profile 集合必须恰好是两个公共 ID，缺失、多余或类型错误都算歧义。"""
    profiles = _document("routine")["profiles"]
    with pytest.raises(RecordingConfigError, match="exactly two"):
        load_recording_settings(
            _write_config(
                tmp_path,
                _document("routine", profiles={**profiles, "debug": _profile_section()}),
            )
        )
    with pytest.raises(RecordingConfigError, match="exactly two"):
        load_recording_settings(_write_config(tmp_path, _document("routine", profiles={})))
    with pytest.raises(RecordingConfigError, match="profiles"):
        load_recording_settings(_write_config(tmp_path, _document("routine", profiles=None)))


def test_profile_id_not_in_profiles_is_ambiguous(tmp_path) -> None:
    """profile_id 必须指向两个已定义 profile 之一。"""
    document = _document("acceptance_raw")
    document["profiles"] = {"acceptance_raw": _profile_section()}
    with pytest.raises(RecordingConfigError, match="routine"):
        load_recording_settings(_write_config(tmp_path, document))


def test_duplicate_invalid_topics_and_contradictory_space_limits_still_fail(tmp_path) -> None:
    """既有 fail-closed 语义在双 profile 结构下保持。"""
    duplicate = _document("routine")
    duplicate["profiles"]["routine"] = _profile_section(topics=RAW_TOPICS + ["/diagnostics"])
    with pytest.raises(RecordingConfigError, match="duplicates"):
        load_recording_settings(_write_config(tmp_path, duplicate))

    relative_topic = _document("routine")
    relative_topic["profiles"]["routine"] = _profile_section(
        topics=RAW_TOPICS[:5] + ["cmd_vel_safe"]
    )
    with pytest.raises(RecordingConfigError, match="invalid absolute ROS topic"):
        load_recording_settings(_write_config(tmp_path, relative_topic))

    contradictory = _document("routine")
    contradictory["min_start_free_space_bytes"] = 1024
    contradictory["min_runtime_free_space_bytes"] = 2048
    with pytest.raises(RecordingConfigError, match="min_start_free_space_bytes"):
        load_recording_settings(_write_config(tmp_path, contradictory))


def test_manifest_schema_requires_recording_profile_id() -> None:
    """manifest.recording 必须携带 profile_id，且只接受两个公共 ID。"""
    schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    recording_schema = schema["properties"]["recording"]
    assert "profile_id" in recording_schema["required"]

    fragment = {
        "config_path": "config/recording.yaml",
        "config_sha256": "a" * 64,
        "storage_config_path": "config/mcap_writer_options.yaml",
        "storage_config_sha256": "b" * 64,
        "recorder_exit_code": 0,
        "log_path": "recording/rosbag.log",
    }
    validator = jsonschema.Draft202012Validator(recording_schema)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(fragment)
    for profile_id in PROFILE_IDS:
        validator.validate({**fragment, "profile_id": profile_id})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**fragment, "profile_id": "debug"})


def test_snapshot_freezes_profile_id_and_reproducible_hashes(tmp_path) -> Path:
    """快照必须携带 profile_id，且两次快照哈希逐字节一致。"""
    source = _write_config(tmp_path / "src", _document("acceptance_raw"))
    settings = load_recording_settings(source)

    first = snapshot_recording_settings(settings, tmp_path / "run-a" / "config")
    second = snapshot_recording_settings(settings, tmp_path / "run-b" / "config")
    assert first["profile_id"] == "acceptance_raw"
    assert first["config_sha256"] == second["config_sha256"]
    assert first["storage_config_sha256"] == second["storage_config_sha256"]
    assert first["config_path"] == "config/recording.yaml"
    frozen = yaml.safe_load(
        (tmp_path / "run-a" / "config" / "recording.yaml").read_text(encoding="utf-8")
    )
    assert frozen["profile_id"] == "acceptance_raw"
    return source
