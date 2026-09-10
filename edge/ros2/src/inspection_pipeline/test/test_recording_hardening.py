"""Tests for fail-closed recording policy and fast rosbag checks."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from inspection_pipeline.bag_health import BagHealthError, inspect_bag
from inspection_pipeline.recording import (
    RecordingConfigError,
    load_recording_settings,
    preflight_storage,
    scan_recorder_log,
)


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def write_minimal_bag(bag: Path) -> dict:
    bag.mkdir()
    (bag / "run_0.mcap").write_bytes(b"\x89MCAP0\r\npayload\x89MCAP0\r\n")
    metadata = {
        "rosbag2_bagfile_information": {
            "storage_identifier": "mcap",
            "relative_file_paths": ["run_0.mcap"],
            "message_count": 2,
            "duration": {"nanoseconds": 1_000_000_000},
            "topics_with_message_count": [
                {
                    "topic_metadata": {"name": "/required"},
                    "message_count": 2,
                }
            ],
        }
    }
    (bag / "metadata.yaml").write_text(
        yaml.safe_dump(metadata),
        encoding="utf-8",
    )
    return metadata


def test_production_recording_policy_builds_bounded_mcap_command() -> None:
    settings = load_recording_settings(CONFIG_DIR / "recording.yaml")
    command = settings.recorder_command(
        Path("/data/scout_runs/run/bag/run"),
        storage_config_path=Path("/data/scout_runs/run/config/mcap_writer_options.yaml"),
    )

    assert settings.max_bagfile_size_bytes == 2 * 1024**3
    assert settings.max_bagfile_duration_seconds == 300
    # The measured Jetson burst is 82,184,925 bytes in one second; 64 MiB drops messages.
    assert settings.max_cache_size_bytes == 256 * 1024**2
    assert settings.max_run_size_bytes == 100 * 1024**3
    assert command[command.index("--max-bag-size") + 1] == "2147483648"
    assert command[command.index("--max-cache-size") + 1] == "268435456"
    assert command[command.index("--storage-preset-profile") + 1] == "zstd_fast"
    assert command[command.index("--storage-config-file") + 1].endswith(
        "config/mcap_writer_options.yaml"
    )
    assert "/camera/image_raw" in command


def test_recording_policy_rejects_unknown_fields_and_disabled_crc(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load((CONFIG_DIR / "recording.yaml").read_text())
    storage = yaml.safe_load((CONFIG_DIR / "mcap_writer_options.yaml").read_text())
    config_path = tmp_path / "recording.yaml"
    storage_path = tmp_path / "mcap_writer_options.yaml"
    storage_path.write_text(yaml.safe_dump(storage), encoding="utf-8")
    document["unexpected"] = True
    config_path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(RecordingConfigError, match="unknown keys"):
        load_recording_settings(config_path)

    document.pop("unexpected")
    storage["noChunkCRC"] = True
    storage_path.write_text(yaml.safe_dump(storage), encoding="utf-8")
    config_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(RecordingConfigError, match="chunk CRC"):
        load_recording_settings(config_path)


def test_storage_preflight_fsyncs_without_leaving_probe(tmp_path: Path) -> None:
    output = tmp_path / "runs"
    health = preflight_storage(output)

    assert health.free_bytes > 0
    assert health.run_size_bytes == 0
    assert list(output.iterdir()) == []


def test_recorder_log_scanner_fails_on_message_loss_and_io_errors(
    tmp_path: Path,
) -> None:
    log = tmp_path / "rosbag.log"
    log.write_text("recorder started\n", encoding="utf-8")
    offset, reason = scan_recorder_log(log, 0)
    assert reason is None

    with log.open("a", encoding="utf-8") as stream:
        stream.write("Message queue starved. Messages will be lost\n")
    offset, reason = scan_recorder_log(log, offset)
    assert reason == "recorder_cache_message_loss"

    log.write_text("write failed: No space left on device\n", encoding="utf-8")
    _offset, reason = scan_recorder_log(log, offset)
    assert reason == "recorder_storage_io_error"


def test_bag_health_requires_positive_count_for_required_topics(tmp_path: Path) -> None:
    bag = tmp_path / "bag"
    bag.mkdir()
    (bag / "run_0.mcap").write_bytes(
        b"\x89MCAP0\r\npayload\x89MCAP0\r\n"
    )
    metadata = {
        "rosbag2_bagfile_information": {
            "storage_identifier": "mcap",
            "relative_file_paths": ["run_0.mcap"],
            "message_count": 1,
            "duration": {"nanoseconds": 1_000_000_000},
            "topics_with_message_count": [
                {
                    "topic_metadata": {"name": "/required"},
                    "message_count": 0,
                },
                {
                    "topic_metadata": {"name": "/other"},
                    "message_count": 1,
                },
            ],
        }
    }
    (bag / "metadata.yaml").write_text(
        yaml.safe_dump(metadata),
        encoding="utf-8",
    )

    with pytest.raises(BagHealthError, match="contain no messages"):
        inspect_bag(bag, required_topics=["/required"])


def test_bag_health_rejects_missing_mcap_footer(tmp_path: Path) -> None:
    bag = tmp_path / "bag"
    bag.mkdir()
    (bag / "run_0.mcap").write_bytes(b"\x89MCAP0\r\nincomplete-record")
    metadata = {
        "rosbag2_bagfile_information": {
            "storage_identifier": "mcap",
            "relative_file_paths": ["run_0.mcap"],
            "message_count": 1,
            "duration": {"nanoseconds": 1_000_000_000},
            "topics_with_message_count": [
                {
                    "topic_metadata": {"name": "/required"},
                    "message_count": 1,
                }
            ],
        }
    }
    (bag / "metadata.yaml").write_text(
        yaml.safe_dump(metadata),
        encoding="utf-8",
    )

    with pytest.raises(BagHealthError, match="header/footer envelope"):
        inspect_bag(bag, required_topics=["/required"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_reference", "duplicate file paths"),
        ("extra_file", "do not exactly match"),
        ("missing_file", "do not exactly match"),
        ("negative_duration", "duration is invalid"),
        ("topic_count_mismatch", "do not sum"),
    ],
)
def test_bag_health_rejects_injected_inventory_and_metadata_corruption(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    bag = tmp_path / "bag"
    metadata = write_minimal_bag(bag)
    information = metadata["rosbag2_bagfile_information"]
    if mutation == "duplicate_reference":
        information["relative_file_paths"].append("run_0.mcap")
    elif mutation == "extra_file":
        (bag / "unlisted.mcap").write_bytes(b"\x89MCAP0\r\ndata\x89MCAP0\r\n")
    elif mutation == "missing_file":
        information["relative_file_paths"] = ["missing.mcap"]
    elif mutation == "negative_duration":
        information["duration"]["nanoseconds"] = -1
    elif mutation == "topic_count_mismatch":
        information["topics_with_message_count"][0]["message_count"] = 1
    (bag / "metadata.yaml").write_text(
        yaml.safe_dump(metadata),
        encoding="utf-8",
    )

    with pytest.raises(BagHealthError, match=message):
        inspect_bag(bag, required_topics=["/required"])
