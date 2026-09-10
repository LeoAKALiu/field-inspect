"""Export and verify vehicle-side station attempts from finalized MCAP records."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import rosbag2_py
import yaml
from diagnostic_msgs.msg import DiagnosticArray
from rclpy.serialization import deserialize_message

from inspection_pipeline.bag_health import inspect_bag
from inspection_pipeline.fixed_route import load_fixed_route
from inspection_pipeline.run_manifest import write_manifest
from inspection_pipeline.station_attempts import (
    IDENTIFIER, StationEvidenceError, build_station_attempts,
)

ARTIFACT_PATH = "artifacts/stations/attempts.json"
PRODUCER_VERSION = "1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StationEvidenceError(f"expected a JSON object: {path}")
    return value


def _root(path: Path | str) -> Path:
    root = Path(path).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise StationEvidenceError("station evidence requires a plain run directory")
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise StationEvidenceError("station evidence cannot contain symlinks or special files")
    return root.resolve()


def _check_generated_at(value: str) -> None:
    if not isinstance(value, str):
        raise StationEvidenceError("station generation timestamp is missing")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
        raise StationEvidenceError("station generation timestamp must use UTC")


def _samples(bag: Path, topic: str):
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(bag), storage_id="mcap"),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    types = {x.name: x.type for x in reader.get_all_topics_and_types()}
    if types.get(topic) != "diagnostic_msgs/msg/DiagnosticArray":
        raise StationEvidenceError("recorded route status topic is missing or has the wrong type")
    reader.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
    while reader.has_next():
        _, payload, stamp = reader.read_next()
        if len(payload) > 1024 * 1024:
            raise StationEvidenceError("recorded route diagnostic exceeds 1 MiB")
        message = deserialize_message(payload, DiagnosticArray)
        for status in message.status:
            if status.name != "inspection_pipeline/route_orchestrator":
                continue
            values = {x.key: x.value for x in status.values}
            if len(values) != len(status.values):
                raise StationEvidenceError("route diagnostic contains ambiguous duplicate fields")
            yield stamp, values


def _derive(root: Path) -> dict:
    operational = _json(root / "manifest.json")
    run_id = operational.get("run_id")
    if not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id):
        raise StationEvidenceError("invalid run identifier")
    reference = operational.get("fixed_route")
    if not isinstance(reference, dict) or reference.get("path") != "config/fixed_route.yaml":
        raise StationEvidenceError("station evidence requires a frozen fixed route")
    route_path = root / reference["path"]
    route = load_fixed_route(route_path, allow_unverified=True)
    if (reference.get("sha256"), reference.get("route_id")) != (
        route.source_sha256, route.route_id,
    ):
        raise StationEvidenceError("frozen route identity or SHA-256 mismatch")
    bag = root / "bag" / run_id
    if operational.get("bag", {}).get("storage_id") != "mcap":
        raise StationEvidenceError("station evidence requires MCAP")
    system = yaml.safe_load((root / "config/system.yaml").read_text(encoding="utf-8"))
    topic = system.get("route_orchestrator", {}).get("ros__parameters", {}).get(
        "route_status_topic", "/route/status",
    )
    if not isinstance(topic, str) or not topic.startswith("/"):
        raise StationEvidenceError("invalid frozen route status topic")
    inspect_bag(bag, required_topics=[topic])
    metadata = yaml.safe_load((bag / "metadata.yaml").read_text(encoding="utf-8"))
    info = metadata["rosbag2_bagfile_information"]
    start = info["starting_time"]["nanoseconds_since_epoch"]
    end = start + info["duration"]["nanoseconds"]
    inputs = sorted([
        root / "config/system.yaml", root / "config/recording.yaml", route_path,
        bag / "metadata.yaml", *bag.rglob("*.mcap"),
    ])

    def inventory():
        return [{"path": x.relative_to(root).as_posix(), "sha256": _sha256(x)} for x in inputs]

    sources = inventory()
    execution = operational.get("route_execution")
    if not isinstance(execution, dict):
        raise StationEvidenceError("station evidence requires the run's route execution")
    attempts = build_station_attempts(
        _samples(bag, topic), run_id=run_id, route_id=route.route_id,
        route_sha256=route.source_sha256, station_ids=tuple(x.station_id for x in route.stations),
        bag_start_ns=start, bag_end_ns=end, run_status=operational.get("status"),
        expected_execution=execution,
    )
    if sources != inventory():
        raise StationEvidenceError("station evidence sources changed during extraction")
    return {
        "schema_version": "1.0", "artifact_kind": "vehicle_station_attempts",
        "producer": "inspection_station_attempts_export", "producer_version": PRODUCER_VERSION,
        "run_id": run_id, "run_status": operational["status"],
        "run_started_at": operational.get("started_at"),
        "run_ended_at": operational.get("ended_at"),
        "route_id": route.route_id, "route_source_kind": route.source_kind,
        "route_execution": execution, "source_topic": topic, "source_files": sources,
        "attempts": attempts,
    }


def export_station_attempts(run_dir: Path | str) -> Path:
    """Write an idempotent station artifact and atomically register it in the run."""
    try:
        root = _root(run_dir)
        manifest_path = root / "manifest.json"
        original_manifest = manifest_path.read_bytes()
        document = _derive(root)
        path = root / ARTIFACT_PATH
        if path.exists():
            existing = _json(path)
            generated_at = existing.pop("generated_at", None)
            if existing != document or not isinstance(generated_at, str):
                raise StationEvidenceError(
                    "existing station artifact conflicts; preserve it for review"
                )
            _check_generated_at(generated_at)
        else:
            generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            write_manifest(path, {**document, "generated_at": generated_at})
        if manifest_path.read_bytes() != original_manifest:
            raise StationEvidenceError("run manifest changed during station export")
        operational = json.loads(original_manifest)
        operational["station_attempts"] = {
            "path": ARTIFACT_PATH, "sha256": _sha256(path), "generated_at": generated_at,
            "producer_version": PRODUCER_VERSION,
        }
        write_manifest(manifest_path, operational)
        return path
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError,
            yaml.YAMLError) as exc:
        if isinstance(exc, StationEvidenceError):
            raise
        raise StationEvidenceError(f"cannot export station evidence: {exc}") from exc


def validate_station_attempts_artifact(run_dir: Path | str) -> str | None:
    """Recompute an indexed artifact from MCAP; allow explicitly unindexed legacy runs."""
    try:
        root = _root(run_dir)
        reference = _json(root / "manifest.json").get("station_attempts")
        path = root / ARTIFACT_PATH
        if reference is None and not path.exists():
            return None
        if (not isinstance(reference, dict) or set(reference) != {
                "path", "sha256", "generated_at", "producer_version"}
                or reference.get("path") != ARTIFACT_PATH
                or reference.get("sha256") != _sha256(path)
                or reference.get("producer_version") != PRODUCER_VERSION):
            raise StationEvidenceError("station artifact index or SHA-256 mismatch")
        document = _json(path)
        generated_at = document.pop("generated_at", None)
        if generated_at != reference["generated_at"] or not isinstance(generated_at, str):
            raise StationEvidenceError("station generation timestamp mismatch")
        _check_generated_at(generated_at)
        if document != _derive(root):
            raise StationEvidenceError("station artifact contradicts the recorded route evidence")
        return ARTIFACT_PATH
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError,
            yaml.YAMLError) as exc:
        if isinstance(exc, StationEvidenceError):
            raise
        raise StationEvidenceError(f"invalid station evidence: {exc}") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        path = export_station_attempts(args.run_dir)
    except StationEvidenceError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"valid": True, "path": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
