"""Validation helpers for the normalized PCD artifact contract."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
EXPECTED_FIELDS = ("x", "y", "z", "intensity")
MAX_PCD_HEADER_BYTES = 16 * 1024


class PointCloudError(ValueError):
    """A point-cloud artifact is unsafe, incomplete, or inconsistent."""


@dataclass(frozen=True)
class PcdHeader:
    """The strict PCD subset emitted by the PCL helper."""

    version: str
    fields: tuple[str, ...]
    sizes: tuple[int, ...]
    types: tuple[str, ...]
    counts: tuple[int, ...]
    width: int
    height: int
    points: int
    data: str
    payload_offset: int


def sha256(path: Path) -> str:
    """Hash one regular artifact without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PointCloudError(f"{field} must be a positive integer")
    return value


def _finite_positive(value: Any, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise PointCloudError(f"{field} must be a finite positive number")
    return float(value)


def _sha256_value(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise PointCloudError(f"{field} must be a SHA-256 digest")
    return value


def read_pcd_header(path: Path) -> PcdHeader:
    """Read and validate the portable binary XYZI PCD subset."""
    if path.is_symlink() or not path.is_file():
        raise PointCloudError(f"PCD must be a regular file: {path}")
    values: dict[str, list[str]] = {}
    payload_offset = 0
    try:
        with path.open("rb") as stream:
            while payload_offset < MAX_PCD_HEADER_BYTES:
                line = stream.readline()
                if not line:
                    break
                payload_offset += len(line)
                try:
                    decoded = line.decode("ascii").strip()
                except UnicodeDecodeError as exc:
                    raise PointCloudError("PCD header must be ASCII") from exc
                if not decoded or decoded.startswith("#"):
                    continue
                parts = decoded.split()
                key = parts[0].upper()
                if key in values:
                    raise PointCloudError(f"PCD header repeats {key}")
                values[key] = parts[1:]
                if key == "DATA":
                    break
    except OSError as exc:
        raise PointCloudError(f"cannot read PCD header: {exc}") from exc
    required = {
        "VERSION",
        "FIELDS",
        "SIZE",
        "TYPE",
        "COUNT",
        "WIDTH",
        "HEIGHT",
        "POINTS",
        "DATA",
    }
    if not required.issubset(values) or "DATA" not in values:
        raise PointCloudError("PCD header is incomplete")
    try:
        version = values["VERSION"][0]
        fields = tuple(values["FIELDS"])
        sizes = tuple(int(item) for item in values["SIZE"])
        types = tuple(values["TYPE"])
        counts = tuple(int(item) for item in values["COUNT"])
        width = int(values["WIDTH"][0])
        height = int(values["HEIGHT"][0])
        points = int(values["POINTS"][0])
        data = values["DATA"][0]
    except (IndexError, ValueError) as exc:
        raise PointCloudError("PCD header contains invalid values") from exc
    header = PcdHeader(
        version=version,
        fields=fields,
        sizes=sizes,
        types=types,
        counts=counts,
        width=width,
        height=height,
        points=points,
        data=data,
        payload_offset=payload_offset,
    )
    if version not in {".7", "0.7"}:
        raise PointCloudError("PCD version must be 0.7")
    if fields != EXPECTED_FIELDS:
        raise PointCloudError(f"PCD fields must be {' '.join(EXPECTED_FIELDS)}")
    if sizes != (4, 4, 4, 4) or types != ("F", "F", "F", "F"):
        raise PointCloudError("PCD XYZI fields must be FLOAT32")
    if counts != (1, 1, 1, 1):
        raise PointCloudError("PCD fields must each have count 1")
    if width <= 0 or height != 1 or points != width:
        raise PointCloudError("PCD must be one non-empty unorganized point cloud")
    if data != "binary":
        raise PointCloudError("PCD DATA must use binary encoding")
    expected_size = payload_offset + points * 16
    if path.stat().st_size != expected_size:
        raise PointCloudError("PCD payload size does not match its header")
    return header


def _bounds(value: Any, field: str, maximum: float) -> dict[str, list[float]]:
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        raise PointCloudError(f"{field} must contain min and max")
    parsed: dict[str, list[float]] = {}
    for key in ("min", "max"):
        vector = value[key]
        if not isinstance(vector, list) or len(vector) != 3:
            raise PointCloudError(f"{field}.{key} must contain three numbers")
        parsed[key] = []
        for item in vector:
            if (
                not isinstance(item, (int, float))
                or isinstance(item, bool)
                or not math.isfinite(item)
                or abs(item) > maximum
            ):
                raise PointCloudError(f"{field}.{key} exceeds the coordinate contract")
            parsed[key].append(float(item))
    if any(low > high for low, high in zip(parsed["min"], parsed["max"])):
        raise PointCloudError(f"{field} min exceeds max")
    return parsed


def validate_pointcloud_manifest(
    document: dict[str, Any],
    *,
    pointcloud_root: Path,
    run_id: str,
    alignment_id: str,
    alignment_sha256: str,
    alignment_translation: tuple[float, ...],
    alignment_rotation: tuple[float, ...],
    system_sha256: str,
    fast_lio_sha256: str,
    export_config_sha256: str,
    storage_id: str,
    contract_authority: dict[str, Any],
) -> list[str]:
    """Cross-check a chunk manifest against its run and every PCD payload."""
    if not isinstance(document, dict):
        raise PointCloudError("point-cloud manifest must be a JSON object")
    if document.get("schema_version") != "1.0":
        raise PointCloudError("point-cloud manifest schema_version must be 1.0")
    if document.get("run_id") != run_id:
        raise PointCloudError("point-cloud manifest run_id mismatch")
    if document.get("coordinate_system") != "scene_local_yup":
        raise PointCloudError("point-cloud coordinate system mismatch")
    source = document.get("source")
    if not isinstance(source, dict):
        raise PointCloudError("point-cloud source provenance is missing")
    if source.get("topic") != "/cloud_registered":
        raise PointCloudError("point-cloud source topic must be /cloud_registered")
    if source.get("type") != "sensor_msgs/msg/PointCloud2":
        raise PointCloudError("point-cloud source_type mismatch")
    if source.get("fields") != list(EXPECTED_FIELDS):
        raise PointCloudError("point-cloud source fields mismatch")
    if not isinstance(source.get("frame_id"), str) or not source["frame_id"]:
        raise PointCloudError("point-cloud source_frame is missing")
    if source.get("bag_storage_id") != storage_id:
        raise PointCloudError("point-cloud bag storage mismatch")
    if source.get("bag_path") != f"bag/{run_id}":
        raise PointCloudError("point-cloud bag path mismatch")
    source_messages = _positive_integer(source.get("message_count"), "source.message_count")
    source_points = _positive_integer(
        source.get("input_point_count"), "source.input_point_count"
    )
    header_range = source.get("header_stamp_ns")
    bag_range = source.get("bag_stamp_ns")
    for value, field in (
        (header_range, "source.header_stamp_ns"),
        (bag_range, "source.bag_stamp_ns"),
    ):
        if not isinstance(value, dict) or set(value) != {"first", "last"}:
            raise PointCloudError(f"{field} must contain first and last")
        first = _positive_integer(value["first"], f"{field}.first")
        last = _positive_integer(value["last"], f"{field}.last")
        if first > last:
            raise PointCloudError(f"{field} is reversed")
    processor = document.get("processor")
    if not isinstance(processor, dict):
        raise PointCloudError("point-cloud processor provenance is missing")
    if processor.get("backend") != "pcl::VoxelGrid+PCDWriter":
        raise PointCloudError("point-cloud processor backend mismatch")
    if not isinstance(processor.get("pcl_version"), str) or not processor["pcl_version"]:
        raise PointCloudError("point-cloud PCL version is missing")
    if processor.get("writer_format") != "pcd-0.7-binary-xyzi":
        raise PointCloudError("point-cloud output format mismatch")
    if processor.get("selection_policy") != "all_registered_scans_by_header_time_window":
        raise PointCloudError("point-cloud selection policy mismatch")
    chunk_duration_ns = _positive_integer(
        processor.get("chunk_duration_ns"), "processor.chunk_duration_ns"
    )
    _finite_positive(processor.get("voxel_leaf_m"), "processor.voxel_leaf_m")
    limits = processor.get("limits")
    if not isinstance(limits, dict):
        raise PointCloudError("point-cloud resource limits are missing")
    max_message = _positive_integer(
        limits.get("max_input_points_per_message"),
        "processor.limits.max_input_points_per_message",
    )
    max_chunk_input = _positive_integer(
        limits.get("max_input_points_per_chunk"),
        "processor.limits.max_input_points_per_chunk",
    )
    max_chunk_output = _positive_integer(
        limits.get("max_output_points_per_chunk"),
        "processor.limits.max_output_points_per_chunk",
    )
    max_chunks = _positive_integer(
        limits.get("max_chunks"), "processor.limits.max_chunks"
    )
    max_duration_ns = _positive_integer(
        limits.get("max_duration_ns"), "processor.limits.max_duration_ns"
    )
    max_coordinate = _finite_positive(
        limits.get("max_abs_coordinate_m"),
        "processor.limits.max_abs_coordinate_m",
    )
    config = processor.get("config")
    if not isinstance(config, dict) or config.get("path") != (
        "config/pointcloud_export.yaml"
    ):
        raise PointCloudError("point-cloud export config path mismatch")
    if _sha256_value(config.get("sha256"), "processor.config.sha256") != (
        export_config_sha256
    ):
        raise PointCloudError("point-cloud export config SHA-256 mismatch")
    if header_range["last"] - header_range["first"] > max_duration_ns:
        raise PointCloudError("point-cloud source exceeds max_duration_ns")
    frame_alias = document.get("frame_alias")
    if not isinstance(frame_alias, dict) or frame_alias != {
        "parent_frame": document.get("map_frame"),
        "child_frame": source.get("frame_id"),
        "identity": True,
    }:
        raise PointCloudError("point-cloud map/source frame alias mismatch")
    if document.get("extrinsics_verified") is not True:
        raise PointCloudError("point-cloud provenance requires verified extrinsics")
    if _sha256_value(document.get("system_config_sha256"), "system_config_sha256") != (
        system_sha256
    ):
        raise PointCloudError("point-cloud system config SHA-256 mismatch")
    fast_lio = document.get("fast_lio_config")
    if not isinstance(fast_lio, dict):
        raise PointCloudError("point-cloud FAST-LIO config provenance is missing")
    if fast_lio.get("path") != "config/fast_lio.yaml":
        raise PointCloudError("point-cloud FAST-LIO config path mismatch")
    if _sha256_value(fast_lio.get("sha256"), "fast_lio_config.sha256") != (
        fast_lio_sha256
    ):
        raise PointCloudError("point-cloud FAST-LIO config SHA-256 mismatch")
    alignment = document.get("alignment")
    if not isinstance(alignment, dict) or alignment.get("alignment_id") != alignment_id:
        raise PointCloudError("point-cloud alignment_id mismatch")
    if alignment.get("verified") is not True:
        raise PointCloudError("point-cloud alignment must be verified")
    if alignment.get("source_kind") not in {
        "onsite_verified",
        "synthetic_contract_fixture",
    }:
        raise PointCloudError("point-cloud alignment source_kind is invalid")
    if _sha256_value(alignment.get("sha256"), "alignment.sha256") != alignment_sha256:
        raise PointCloudError("point-cloud alignment SHA-256 mismatch")
    if alignment.get("transform") != {
        "translation_m": list(alignment_translation),
        "rotation_xyzw": list(alignment_rotation),
    }:
        raise PointCloudError("point-cloud alignment transform mismatch")
    if document.get("contract_authority") != contract_authority:
        raise PointCloudError("point-cloud contract authority mismatch")
    exporter = document.get("exporter")
    if not isinstance(exporter, dict):
        raise PointCloudError("point-cloud exporter provenance is missing")
    if exporter.get("package") != "inspection_pipeline":
        raise PointCloudError("point-cloud exporter package mismatch")
    if not isinstance(exporter.get("version"), str) or not exporter["version"]:
        raise PointCloudError("point-cloud exporter version is missing")
    if not isinstance(exporter.get("git_commit"), str) or re.fullmatch(
        r"unknown|[a-f0-9]{7,40}", exporter["git_commit"]
    ) is None:
        raise PointCloudError("point-cloud exporter git commit is missing")

    chunks = document.get("chunks")
    if not isinstance(chunks, list) or not chunks or len(chunks) > max_chunks:
        raise PointCloudError("point-cloud chunks must be a non-empty bounded list")
    artifact_paths: list[str] = []
    total_messages = 0
    total_input_points = 0
    previous_window = -1
    previous_header_end = 0
    previous_bag_end = 0
    for sequence, chunk in enumerate(chunks):
        if not isinstance(chunk, dict) or chunk.get("seq") != sequence:
            raise PointCloudError("point-cloud chunk sequence must be contiguous")
        window_index = chunk.get("window_index")
        if (
            not isinstance(window_index, int)
            or isinstance(window_index, bool)
            or window_index < 0
            or window_index <= previous_window
        ):
            raise PointCloudError("point-cloud chunk window_index must increase")
        previous_window = window_index
        relative = f"artifacts/pointcloud/chunks/{sequence:06d}.pcd"
        if chunk.get("path") != relative:
            raise PointCloudError("point-cloud chunk path mismatch")
        pcd_path = pointcloud_root / "chunks" / f"{sequence:06d}.pcd"
        header = read_pcd_header(pcd_path)
        if _sha256_value(chunk.get("sha256"), "chunk.sha256") != sha256(pcd_path):
            raise PointCloudError("point-cloud chunk SHA-256 mismatch")
        output_points = _positive_integer(
            chunk.get("output_point_count"), "chunk.output_point_count"
        )
        if output_points != header.points or output_points > max_chunk_output:
            raise PointCloudError("point-cloud chunk output count mismatch")
        input_points = _positive_integer(
            chunk.get("input_point_count"), "chunk.input_point_count"
        )
        messages = _positive_integer(chunk.get("message_count"), "chunk.message_count")
        if input_points > max_chunk_input or input_points < messages:
            raise PointCloudError("point-cloud chunk input count is inconsistent")
        if input_points > messages * max_message:
            raise PointCloudError("point-cloud chunk message limit is inconsistent")
        chunk_header = chunk.get("header_stamp_ns")
        chunk_bag = chunk.get("bag_stamp_ns")
        for value, field, previous in (
            (chunk_header, "chunk.header_stamp_ns", previous_header_end),
            (chunk_bag, "chunk.bag_stamp_ns", previous_bag_end),
        ):
            if not isinstance(value, dict) or set(value) != {"first", "last"}:
                raise PointCloudError(f"{field} must contain first and last")
            first = _positive_integer(value["first"], f"{field}.first")
            last = _positive_integer(value["last"], f"{field}.last")
            if first > last or first < previous:
                raise PointCloudError(f"{field} is not monotonic")
        expected_window = (
            chunk_header["first"] - header_range["first"]
        ) // chunk_duration_ns
        final_window = (
            chunk_header["last"] - header_range["first"]
        ) // chunk_duration_ns
        if expected_window != window_index or final_window != window_index:
            raise PointCloudError("point-cloud chunk window assignment mismatch")
        previous_header_end = chunk_header["last"]
        previous_bag_end = chunk_bag["last"]
        _bounds(chunk.get("bounds_m"), "chunk.bounds_m", max_coordinate)
        total_messages += messages
        total_input_points += input_points
        artifact_paths.append(relative)
    if total_messages != source_messages or total_input_points != source_points:
        raise PointCloudError("point-cloud chunk totals do not match source totals")
    if chunks[0]["header_stamp_ns"]["first"] != header_range["first"] or (
        chunks[-1]["header_stamp_ns"]["last"] != header_range["last"]
    ):
        raise PointCloudError("point-cloud chunk header range does not cover the source")
    if chunks[0]["bag_stamp_ns"]["first"] != bag_range["first"] or (
        chunks[-1]["bag_stamp_ns"]["last"] != bag_range["last"]
    ):
        raise PointCloudError("point-cloud chunk bag range does not cover the source")
    return artifact_paths
