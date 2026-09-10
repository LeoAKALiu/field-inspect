"""Strict rosbag2 metadata and file-inventory validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

MCAP_MAGIC = b"\x89MCAP0\r\n"


class BagHealthError(ValueError):
    """A rosbag tree is incomplete or contradicts its metadata."""


@dataclass(frozen=True)
class BagSummary:
    """Evidence collected without deserializing recorded messages."""

    size_bytes: int
    file_count: int
    message_count: int
    duration_nanoseconds: int
    topics: tuple[str, ...]
    topic_counts: tuple[tuple[str, int], ...]

    def manifest_fields(self) -> dict[str, int]:
        return {
            "size_bytes": self.size_bytes,
            "file_count": self.file_count,
            "message_count": self.message_count,
        }


def _safe_relative_file(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise BagHealthError("rosbag metadata contains an invalid relative file path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or value != relative.as_posix():
        raise BagHealthError("rosbag metadata file paths must be normalized and relative")
    if relative.suffix != ".mcap":
        raise BagHealthError("rosbag metadata must reference only .mcap data files")
    return value


def inspect_bag(
    bag_dir: Path | str,
    *,
    storage_id: str = "mcap",
    required_topics: Iterable[str] = (),
    require_messages: bool = True,
) -> BagSummary:
    """Validate metadata, MCAP inventory and the required recorded topic set."""
    required_topic_set = set(required_topics)
    unresolved = Path(bag_dir).expanduser()
    if unresolved.is_symlink():
        raise BagHealthError("bag directory must not be a symbolic link")
    root = unresolved.resolve()
    if not root.is_dir():
        raise BagHealthError(f"bag directory does not exist: {root}")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise BagHealthError("bag directory contains symbolic links")
    metadata_path = root / "metadata.yaml"
    try:
        document = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        information = document["rosbag2_bagfile_information"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise BagHealthError(f"cannot load rosbag metadata: {exc}") from exc
    if not isinstance(information, dict):
        raise BagHealthError("rosbag metadata information must be a mapping")
    if information.get("storage_identifier") != storage_id:
        raise BagHealthError("rosbag metadata storage_identifier mismatch")
    relative_values = information.get("relative_file_paths")
    if not isinstance(relative_values, list) or not relative_values:
        raise BagHealthError("rosbag metadata has no relative_file_paths")
    relative_files = tuple(_safe_relative_file(value) for value in relative_values)
    if len(set(relative_files)) != len(relative_files):
        raise BagHealthError("rosbag metadata contains duplicate file paths")
    actual_files = tuple(
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*.mcap"))
    )
    if tuple(sorted(relative_files)) != actual_files:
        raise BagHealthError("MCAP files do not exactly match rosbag metadata")
    size_bytes = 0
    for relative in relative_files:
        path = root / relative
        if not path.is_file() or path.stat().st_size <= len(MCAP_MAGIC) * 2:
            raise BagHealthError(f"MCAP data file is missing or empty: {relative}")
        try:
            with path.open("rb") as stream:
                leading_magic = stream.read(len(MCAP_MAGIC))
                stream.seek(-len(MCAP_MAGIC), 2)
                trailing_magic = stream.read(len(MCAP_MAGIC))
        except OSError as exc:
            raise BagHealthError(f"cannot read MCAP envelope: {relative}") from exc
        if leading_magic != MCAP_MAGIC or trailing_magic != MCAP_MAGIC:
            raise BagHealthError(
                f"MCAP data file has no complete header/footer envelope: {relative}"
            )
        size_bytes += path.stat().st_size

    message_count = information.get("message_count")
    if (
        not isinstance(message_count, int)
        or isinstance(message_count, bool)
        or message_count < 0
    ):
        raise BagHealthError("rosbag metadata message_count is invalid")
    if require_messages and message_count == 0:
        raise BagHealthError("rosbag contains no messages")
    duration = information.get("duration")
    duration_nanoseconds = (
        duration.get("nanoseconds") if isinstance(duration, dict) else None
    )
    if (
        not isinstance(duration_nanoseconds, int)
        or isinstance(duration_nanoseconds, bool)
        or duration_nanoseconds < 0
    ):
        raise BagHealthError("rosbag metadata duration is invalid")
    topic_entries = information.get("topics_with_message_count")
    if not isinstance(topic_entries, list):
        raise BagHealthError("rosbag metadata topics_with_message_count is invalid")
    topics: list[str] = []
    topic_counts: dict[str, int] = {}
    topic_message_total = 0
    for entry in topic_entries:
        try:
            topic = entry["topic_metadata"]["name"]
            count = entry["message_count"]
        except (KeyError, TypeError) as exc:
            raise BagHealthError("rosbag metadata contains an invalid topic entry") from exc
        if not isinstance(topic, str) or not topic.startswith("/"):
            raise BagHealthError("rosbag metadata contains an invalid topic name")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise BagHealthError("rosbag metadata contains an invalid topic count")
        topics.append(topic)
        topic_counts[topic] = count
        topic_message_total += count
    if len(set(topics)) != len(topics):
        raise BagHealthError("rosbag metadata contains duplicate topics")
    if topic_message_total != message_count:
        raise BagHealthError("rosbag topic counts do not sum to message_count")
    missing_topics = required_topic_set - set(topics)
    if missing_topics:
        raise BagHealthError(
            "rosbag is missing required topics: " + ", ".join(sorted(missing_topics))
        )
    empty_required_topics = {
        topic for topic in required_topic_set if topic_counts.get(topic, 0) == 0
    }
    if empty_required_topics:
        raise BagHealthError(
            "rosbag required topics contain no messages: "
            + ", ".join(sorted(empty_required_topics))
        )
    return BagSummary(
        size_bytes=size_bytes,
        file_count=len(relative_files),
        message_count=message_count,
        duration_nanoseconds=duration_nanoseconds,
        topics=tuple(sorted(topics)),
        topic_counts=tuple(sorted(topic_counts.items())),
    )
