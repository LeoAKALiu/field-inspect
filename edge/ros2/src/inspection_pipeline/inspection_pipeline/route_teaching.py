"""Generate an unverified fixed-route candidate from a finalized teaching run."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import rosbag2_py
import scipy
import shapely
import yaml
from ament_index_python.packages import get_package_share_directory
from scipy.spatial.transform import Rotation
from shapely import geos
from shapely.geometry import LineString

from inspection_pipeline.fixed_route import (
    HARD_MAX_LINEAR_MPS,
    IDENTIFIER_PATTERN,
    MAX_ROUTE_POSES,
    FixedRouteError,
    RoutePose,
    load_fixed_route,
    route_pose_length,
)
from inspection_pipeline.trajectory import (
    RigidTransform,
    TrajectoryError,
    compose,
    inverse,
    validate_transform,
)
from inspection_pipeline.trajectory_export import (
    _json_bytes,
    _load_run_contract,
    _read_odometry_samples,
)

SOURCE_TYPE = "nav_msgs/msg/Odometry"
ROUTE_FILENAME = "fixed_route.candidate.yaml"
PROVENANCE_FILENAME = "fixed_route.candidate.provenance.json"
CONFIG_FILENAME = "route_teaching.yaml"
SPEC_FILENAME = "route_teaching_spec.yaml"
SETTING_KEYS = {
    "schema_version",
    "duplicate_epsilon_m",
    "max_duplicate_yaw_change_rad",
    "simplify_tolerance_m",
    "resample_spacing_m",
    "max_station_snap_distance_m",
    "max_source_samples",
    "max_output_poses",
    "max_stations",
    "max_duration_ns",
    "max_header_gap_ns",
    "max_position_jump_m",
    "max_motion_heading_error_rad",
    "max_abs_coordinate_m",
    "max_tilt_rad",
    "max_route_length_m",
}


class RouteTeachingError(ValueError):
    """A teaching run cannot produce a trustworthy route candidate."""


@dataclass(frozen=True)
class PlanarSample:
    """One validated map-to-base sample reduced to the route plane."""

    stamp_ns: int
    x_m: float
    y_m: float
    z_m: float
    yaw_rad: float
    tilt_rad: float


@dataclass(frozen=True)
class StationInput:
    """One human-entered physical station location in the map frame."""

    station_id: str
    x_m: float
    y_m: float
    required: bool
    tag_ids: tuple[str, ...]


@dataclass(frozen=True)
class StationSnap:
    """A station deterministically snapped to one source route sample."""

    station: StationInput
    source_index: int
    distance_m: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: Any, field: str, *, minimum: float = -math.inf) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < minimum
    ):
        raise RouteTeachingError(
            f"{field} must be a finite number >= {minimum}"
        )
    return float(value)


def _positive_integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RouteTeachingError(f"{field} must be a positive integer")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise RouteTeachingError(f"{field} is not a valid identifier")
    return value


def _load_yaml(path: Path, description: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise RouteTeachingError(f"{description} must be a regular file: {path}")
    try:
        raw = path.read_bytes()
        document = yaml.safe_load(raw)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RouteTeachingError(f"cannot load {description}: {exc}") from exc
    if not isinstance(document, dict):
        raise RouteTeachingError(f"{description} must contain a YAML mapping")
    return document, raw


def _load_settings(path: Path) -> tuple[dict[str, Any], bytes]:
    document, raw = _load_yaml(path, "route-teaching config")
    if set(document) != SETTING_KEYS or document.get("schema_version") != "1.0":
        raise RouteTeachingError(
            "route-teaching config fields do not match schema 1.0"
        )
    for field in (
        "max_source_samples",
        "max_output_poses",
        "max_stations",
        "max_duration_ns",
        "max_header_gap_ns",
    ):
        document[field] = _positive_integer(document.get(field), field)
    for field in (
        "duplicate_epsilon_m",
        "max_duplicate_yaw_change_rad",
        "simplify_tolerance_m",
        "resample_spacing_m",
        "max_station_snap_distance_m",
        "max_position_jump_m",
        "max_motion_heading_error_rad",
        "max_abs_coordinate_m",
        "max_tilt_rad",
        "max_route_length_m",
    ):
        document[field] = _finite_number(document.get(field), field, minimum=0.0)
        if document[field] <= 0.0:
            raise RouteTeachingError(f"{field} must be positive")
    if document["max_output_poses"] > MAX_ROUTE_POSES:
        raise RouteTeachingError(
            f"max_output_poses must not exceed {MAX_ROUTE_POSES}"
        )
    if document["simplify_tolerance_m"] < document["duplicate_epsilon_m"]:
        raise RouteTeachingError(
            "simplify_tolerance_m must cover duplicate_epsilon_m"
        )
    if document["resample_spacing_m"] < document["simplify_tolerance_m"]:
        raise RouteTeachingError(
            "resample_spacing_m must cover simplify_tolerance_m"
        )
    if not 0.0 < document["max_duplicate_yaw_change_rad"] <= math.pi:
        raise RouteTeachingError("max_duplicate_yaw_change_rad must be in (0, pi]")
    if not 0.0 < document["max_motion_heading_error_rad"] < math.pi:
        raise RouteTeachingError("max_motion_heading_error_rad must be in (0, pi)")
    if not 0.0 < document["max_tilt_rad"] < math.pi / 2.0:
        raise RouteTeachingError("max_tilt_rad must be in (0, pi/2)")
    return document, raw


def _load_spec(
    path: Path,
    *,
    max_stations: int,
    max_abs_coordinate_m: float,
) -> tuple[dict[str, Any], tuple[StationInput, ...], bytes]:
    document, raw = _load_yaml(path, "route-teaching specification")
    expected = {
        "schema_version",
        "route_id",
        "approved_max_linear_mps",
        "stations",
    }
    if set(document) != expected or document.get("schema_version") != "1.0":
        raise RouteTeachingError(
            "route-teaching specification fields do not match schema 1.0"
        )
    route_id = _identifier(document.get("route_id"), "route_id")
    approved_speed = _finite_number(
        document.get("approved_max_linear_mps"),
        "approved_max_linear_mps",
        minimum=0.0,
    )
    if not 0.0 < approved_speed <= HARD_MAX_LINEAR_MPS:
        raise RouteTeachingError(
            "approved_max_linear_mps must be in (0.0, 0.2]"
        )
    values = document.get("stations")
    if not isinstance(values, list) or not 1 <= len(values) <= max_stations:
        raise RouteTeachingError(
            f"stations must contain 1-{max_stations} entries"
        )
    stations: list[StationInput] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != {
            "station_id",
            "x_m",
            "y_m",
            "required",
            "tag_ids",
        }:
            raise RouteTeachingError(
                f"stations[{index}] must contain only "
                "station_id/x_m/y_m/required/tag_ids"
            )
        station_id = _identifier(value.get("station_id"), f"stations[{index}].station_id")
        if station_id in seen_ids:
            raise RouteTeachingError("station_id values must be unique")
        x_m = _finite_number(value.get("x_m"), f"stations[{index}].x_m")
        y_m = _finite_number(value.get("y_m"), f"stations[{index}].y_m")
        if abs(x_m) > max_abs_coordinate_m or abs(y_m) > max_abs_coordinate_m:
            raise RouteTeachingError("station coordinate exceeds configured bounds")
        required = value.get("required")
        if not isinstance(required, bool):
            raise RouteTeachingError(f"stations[{index}].required must be a boolean")
        raw_tags = value.get("tag_ids")
        if not isinstance(raw_tags, list):
            raise RouteTeachingError(f"stations[{index}].tag_ids must be a list")
        tag_ids = tuple(
            _identifier(item, f"stations[{index}].tag_ids[]")
            for item in raw_tags
        )
        if len(set(tag_ids)) != len(tag_ids):
            raise RouteTeachingError(f"stations[{index}].tag_ids has duplicates")
        stations.append(StationInput(station_id, x_m, y_m, required, tag_ids))
        seen_ids.add(station_id)
    document["route_id"] = route_id
    document["approved_max_linear_mps"] = approved_speed
    return document, tuple(stations), raw


def _wrap_yaw(value: float) -> float:
    wrapped = (value + math.pi) % (2.0 * math.pi) - math.pi
    return math.pi if wrapped == -math.pi and value > 0.0 else wrapped


def _angle_distance(left: float, right: float) -> float:
    return abs(_wrap_yaw(left - right))


def _planar_samples(
    poses: Sequence[Any],
    *,
    base_to_body: RigidTransform,
    input_global_frame: str,
    body_frame: str,
    settings: dict[str, Any],
) -> tuple[list[PlanarSample], dict[str, float]]:
    if not poses:
        raise RouteTeachingError("the teaching bag contains no pose samples")
    if len(poses) > settings["max_source_samples"]:
        raise RouteTeachingError("source pose count exceeds max_source_samples")
    inverse_base_to_body = inverse(base_to_body)
    samples: list[PlanarSample] = []
    previous: Optional[PlanarSample] = None
    maximum_heading_error = 0.0
    for index, pose in enumerate(poses):
        if pose.parent_frame != input_global_frame:
            raise RouteTeachingError(
                f"pose parent frame {pose.parent_frame!r} does not match "
                f"{input_global_frame!r}"
            )
        if pose.child_frame != body_frame:
            raise RouteTeachingError(
                f"pose child frame {pose.child_frame!r} does not match {body_frame!r}"
            )
        if pose.stamp_ns <= 0:
            raise RouteTeachingError("pose timestamp must be positive")
        try:
            validate_transform(pose.map_to_body, f"poses[{index}]")
            map_to_base = compose(pose.map_to_body, inverse_base_to_body)
        except TrajectoryError as exc:
            raise RouteTeachingError(f"invalid source pose: {exc}") from exc
        position = tuple(float(item) for item in map_to_base.translation)
        if any(
            not math.isfinite(item)
            or abs(item) > settings["max_abs_coordinate_m"]
            for item in position
        ):
            raise RouteTeachingError("source pose coordinate exceeds configured bounds")
        rotation = Rotation.from_quat(map_to_base.rotation)
        forward = rotation.apply((1.0, 0.0, 0.0))
        forward_xy = math.hypot(float(forward[0]), float(forward[1]))
        if forward_xy < 1e-9:
            raise RouteTeachingError("vehicle forward axis is vertical")
        yaw = math.atan2(float(forward[1]), float(forward[0]))
        up = rotation.apply((0.0, 0.0, 1.0))
        tilt = math.acos(min(1.0, max(-1.0, float(up[2]))))
        if tilt > settings["max_tilt_rad"]:
            raise RouteTeachingError("source pose tilt exceeds max_tilt_rad")
        sample = PlanarSample(
            pose.stamp_ns,
            position[0],
            position[1],
            position[2],
            _wrap_yaw(yaw),
            tilt,
        )
        if previous is not None:
            if sample.stamp_ns <= previous.stamp_ns:
                raise RouteTeachingError("pose timestamps must be strictly increasing")
            if sample.stamp_ns - previous.stamp_ns > settings["max_header_gap_ns"]:
                raise RouteTeachingError("pose header gap exceeds max_header_gap_ns")
            displacement = math.hypot(
                sample.x_m - previous.x_m,
                sample.y_m - previous.y_m,
            )
            if displacement > settings["max_position_jump_m"]:
                raise RouteTeachingError("pose position jump exceeds max_position_jump_m")
            if displacement > settings["duplicate_epsilon_m"]:
                direction = math.atan2(
                    sample.y_m - previous.y_m,
                    sample.x_m - previous.x_m,
                )
                heading_error = _angle_distance(direction, previous.yaw_rad)
                maximum_heading_error = max(maximum_heading_error, heading_error)
                if heading_error > settings["max_motion_heading_error_rad"]:
                    raise RouteTeachingError(
                        "source motion is incompatible with forward-only route tracking"
                    )
        samples.append(sample)
        previous = sample
    if samples[-1].stamp_ns - samples[0].stamp_ns > settings["max_duration_ns"]:
        raise RouteTeachingError("teaching duration exceeds max_duration_ns")
    return samples, {"max_motion_heading_error_rad": maximum_heading_error}


def _deduplicate_samples(
    samples: Sequence[PlanarSample],
    *,
    epsilon_m: float,
    max_yaw_change_rad: float,
) -> list[PlanarSample]:
    retained: list[PlanarSample] = []
    cluster_yaw = 0.0
    for sample in samples:
        if not retained:
            retained.append(sample)
            cluster_yaw = sample.yaw_rad
            continue
        anchor = retained[-1]
        distance = math.hypot(sample.x_m - anchor.x_m, sample.y_m - anchor.y_m)
        if distance > epsilon_m:
            retained.append(sample)
            cluster_yaw = sample.yaw_rad
            continue
        if _angle_distance(sample.yaw_rad, cluster_yaw) > max_yaw_change_rad:
            raise RouteTeachingError(
                "in-place rotation exceeds max_duplicate_yaw_change_rad"
            )
        retained[-1] = PlanarSample(
            stamp_ns=sample.stamp_ns,
            x_m=anchor.x_m,
            y_m=anchor.y_m,
            z_m=sample.z_m,
            yaw_rad=sample.yaw_rad,
            tilt_rad=sample.tilt_rad,
        )
    if len(retained) < 2:
        raise RouteTeachingError("teaching route has fewer than two distinct positions")
    return retained


def _snap_stations(
    samples: Sequence[PlanarSample],
    stations: Sequence[StationInput],
    *,
    maximum_distance_m: float,
) -> list[StationSnap]:
    coordinates = np.asarray([(item.x_m, item.y_m) for item in samples])
    snaps: list[StationSnap] = []
    previous_index = 0
    for station in stations:
        squared = np.square(coordinates[:, 0] - station.x_m) + np.square(
            coordinates[:, 1] - station.y_m
        )
        source_index = int(np.argmin(squared))
        distance = math.sqrt(float(squared[source_index]))
        if distance > maximum_distance_m:
            raise RouteTeachingError(
                f"station {station.station_id} exceeds max_station_snap_distance_m"
            )
        if source_index <= previous_index:
            raise RouteTeachingError(
                "station source indices must be unique and strictly increasing"
            )
        snaps.append(StationSnap(station, source_index, distance))
        previous_index = source_index
    if snaps[-1].source_index != len(samples) - 1:
        raise RouteTeachingError("the final station must snap to the route endpoint")
    return snaps


def _cumulative_distances(coordinates: np.ndarray) -> np.ndarray:
    deltas = np.diff(coordinates, axis=0)
    distances = np.hypot(deltas[:, 0], deltas[:, 1])
    cumulative = np.concatenate((np.asarray([0.0]), np.cumsum(distances)))
    if len(cumulative) < 2 or np.any(np.diff(cumulative) <= 0.0):
        raise RouteTeachingError("route segment has duplicate or degenerate points")
    return cumulative


def _target_distances(length_m: float, spacing_m: float) -> np.ndarray:
    count = int(math.floor(length_m / spacing_m))
    values = [index * spacing_m for index in range(count + 1)]
    if not values or length_m - values[-1] > 1e-9:
        values.append(length_m)
    else:
        values[-1] = length_m
    return np.asarray(values, dtype=float)


def _round_pose(x_m: float, y_m: float, yaw_rad: float) -> RoutePose:
    return RoutePose(
        x_m=round(float(x_m), 9),
        y_m=round(float(y_m), 9),
        yaw_rad=round(_wrap_yaw(float(yaw_rad)), 9),
    )


def _process_segment(
    samples: Sequence[PlanarSample],
    settings: dict[str, Any],
) -> tuple[list[RoutePose], dict[str, Any]]:
    raw_coordinates = np.asarray([(item.x_m, item.y_m) for item in samples])
    raw_distances = _cumulative_distances(raw_coordinates)
    raw_line = LineString(raw_coordinates)
    if not raw_line.is_simple:
        raise RouteTeachingError("a station segment self-intersects or retraces")
    simplified = raw_line.simplify(
        settings["simplify_tolerance_m"],
        preserve_topology=False,
    )
    if simplified.geom_type != "LineString" or simplified.is_empty:
        raise RouteTeachingError("Douglas-Peucker produced an invalid route segment")
    simplified_coordinates = np.asarray(simplified.coords)
    if len(simplified_coordinates) < 2:
        raise RouteTeachingError("Douglas-Peucker collapsed a route segment")
    if not np.allclose(simplified_coordinates[0], raw_coordinates[0], atol=1e-9):
        raise RouteTeachingError("Douglas-Peucker changed the segment start")
    if not np.allclose(simplified_coordinates[-1], raw_coordinates[-1], atol=1e-9):
        raise RouteTeachingError("Douglas-Peucker changed the segment end")
    if not simplified.is_simple:
        raise RouteTeachingError("Douglas-Peucker introduced a self-intersection")
    deviation = float(raw_line.hausdorff_distance(simplified))
    if deviation > settings["simplify_tolerance_m"] + 1e-9:
        raise RouteTeachingError("Douglas-Peucker exceeded simplify_tolerance_m")
    simplified_distances = _cumulative_distances(simplified_coordinates)
    simplified_length = float(simplified_distances[-1])
    target = _target_distances(
        simplified_length,
        settings["resample_spacing_m"],
    )
    x_values = np.interp(
        target,
        simplified_distances,
        simplified_coordinates[:, 0],
    )
    y_values = np.interp(
        target,
        simplified_distances,
        simplified_coordinates[:, 1],
    )
    raw_yaws = np.unwrap(np.asarray([item.yaw_rad for item in samples]))
    raw_targets = target / simplified_length * float(raw_distances[-1])
    yaw_values = np.interp(raw_targets, raw_distances, raw_yaws)
    output = [
        _round_pose(x_m, y_m, yaw_rad)
        for x_m, y_m, yaw_rad in zip(x_values, y_values, yaw_values)
    ]
    report = {
        "source_pose_count": len(samples),
        "source_length_m": float(raw_distances[-1]),
        "simplified_pose_count": len(simplified_coordinates),
        "simplified_length_m": simplified_length,
        "output_pose_count": len(output),
        "max_simplification_deviation_m": deviation,
    }
    return output, report


def _build_candidate(
    *,
    route_id: str,
    run_id: str,
    approved_speed: float,
    samples: Sequence[PlanarSample],
    snaps: Sequence[StationSnap],
    settings: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    poses: list[RoutePose] = []
    station_values: list[dict[str, Any]] = []
    station_reports: list[dict[str, Any]] = []
    segment_reports: list[dict[str, Any]] = []
    start_index = 0
    for segment_index, snap in enumerate(snaps):
        segment, report = _process_segment(
            samples[start_index:snap.source_index + 1],
            settings,
        )
        if poses:
            segment = segment[1:]
        if not segment:
            raise RouteTeachingError("route segment produced no new output poses")
        poses.extend(segment)
        if len(poses) > settings["max_output_poses"]:
            raise RouteTeachingError("candidate exceeds max_output_poses")
        pose_index = len(poses) - 1
        station_values.append(
            {
                "station_id": snap.station.station_id,
                "pose_index": pose_index,
                "required": snap.station.required,
                "tag_ids": list(snap.station.tag_ids),
            }
        )
        snapped = samples[snap.source_index]
        station_reports.append(
            {
                "station_id": snap.station.station_id,
                "requested_position_m": {
                    "x": snap.station.x_m,
                    "y": snap.station.y_m,
                },
                "snapped_source_index": snap.source_index,
                "snapped_source_stamp_ns": snapped.stamp_ns,
                "snap_distance_m": snap.distance_m,
                "candidate_pose_index": pose_index,
                "candidate_pose": {
                    "x_m": poses[-1].x_m,
                    "y_m": poses[-1].y_m,
                    "yaw_rad": poses[-1].yaw_rad,
                },
                "required": snap.station.required,
                "tag_ids": list(snap.station.tag_ids),
            }
        )
        report["seq"] = segment_index
        report["station_id"] = snap.station.station_id
        segment_reports.append(report)
        start_index = snap.source_index
    length_m = route_pose_length(poses)
    if length_m > settings["max_route_length_m"]:
        raise RouteTeachingError("candidate exceeds max_route_length_m")
    route = {
        "schema_version": "1.0",
        "route_id": route_id,
        "source_kind": "odometry_teaching_candidate",
        "frame_id": "map",
        "verified": False,
        "verified_at": None,
        "source_reference": f"teaching-run:{run_id}",
        "approved_max_linear_mps": approved_speed,
        "poses": [
            {"x_m": pose.x_m, "y_m": pose.y_m, "yaw_rad": pose.yaw_rad}
            for pose in poses
        ],
        "stations": station_values,
    }
    return route, station_reports, segment_reports


def _yaml_bytes(value: dict[str, Any]) -> bytes:
    try:
        return yaml.safe_dump(
            value,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ).encode("utf-8")
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise RouteTeachingError(f"cannot serialize route candidate: {exc}") from exc


def _bag_inventory(bag_dir: Path, storage_id: str) -> list[dict[str, Any]]:
    if bag_dir.is_symlink() or not bag_dir.is_dir():
        raise RouteTeachingError(f"bag directory must be a regular tree: {bag_dir}")
    files = sorted(path for path in bag_dir.rglob("*") if path.is_file())
    if not files or any(path.is_symlink() for path in bag_dir.rglob("*")):
        raise RouteTeachingError("bag tree is empty or contains symbolic links")
    metadata_path = bag_dir / "metadata.yaml"
    document, _raw = _load_yaml(metadata_path, "rosbag metadata")
    try:
        metadata_storage = document["rosbag2_bagfile_information"][
            "storage_identifier"
        ]
    except (KeyError, TypeError) as exc:
        raise RouteTeachingError("rosbag metadata is missing storage_identifier") from exc
    if metadata_storage != storage_id:
        raise RouteTeachingError("rosbag metadata storage_identifier mismatch")
    return [
        {
            "path": path.relative_to(bag_dir).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
    ]


def _git_commit() -> str:
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
        return completed.stdout.strip() or "unknown"
    return "unknown"


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
    return left_files == right_files and all(
        (left / relative).read_bytes() == (right / relative).read_bytes()
        for relative in left_files
    )


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY)
        elif path.is_dir():
            descriptor = os.open(path, os.O_RDONLY)
        else:
            continue
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def export_route_candidate(
    run_dir: Path | str,
    teaching_spec_path: Path | str,
    output_directory: Path | str,
    *,
    teaching_config_path: Optional[Path | str] = None,
    allow_non_mcap_bag: bool = False,
) -> Path:
    """Generate an atomic, non-executable fixed-route candidate directory."""
    unresolved_run = Path(run_dir).expanduser()
    unresolved_spec = Path(teaching_spec_path).expanduser()
    unresolved_output = Path(output_directory).expanduser()
    if unresolved_run.is_symlink() or unresolved_spec.is_symlink():
        raise RouteTeachingError("run and teaching spec must not be symbolic links")
    if unresolved_output.is_symlink():
        raise RouteTeachingError("candidate output must not be a symbolic link")
    source = unresolved_run.resolve()
    spec_source = unresolved_spec.resolve()
    target = unresolved_output.resolve()
    if not source.is_dir():
        raise RouteTeachingError(f"run directory does not exist: {source}")
    if target == source or source in target.parents:
        raise RouteTeachingError(
            "candidate output must be outside the immutable teaching run"
        )
    if teaching_config_path is None:
        config_source = (
            Path(get_package_share_directory("inspection_pipeline"))
            / f"config/{CONFIG_FILENAME}"
        ).resolve()
    else:
        unresolved_config = Path(teaching_config_path).expanduser()
        if unresolved_config.is_symlink():
            raise RouteTeachingError("route-teaching config must not be a symbolic link")
        config_source = unresolved_config.resolve()
    settings, config_bytes = _load_settings(config_source)
    spec, stations, spec_bytes = _load_spec(
        spec_source,
        max_stations=settings["max_stations"],
        max_abs_coordinate_m=settings["max_abs_coordinate_m"],
    )
    try:
        operational, bringup, localization, _health, base_to_body = (
            _load_run_contract(source)
        )
    except TrajectoryError as exc:
        raise RouteTeachingError(f"invalid teaching run contract: {exc}") from exc
    if operational.get("status") != "completed":
        raise RouteTeachingError("teaching run status must be completed")
    if localization.get("map_frame") != "map":
        raise RouteTeachingError("fixed-route teaching requires map_frame=map")
    run_id = str(operational["run_id"])
    bag_reference = operational.get("bag")
    if not isinstance(bag_reference, dict):
        raise RouteTeachingError("operational manifest bag must be a mapping")
    storage_id = str(bag_reference.get("storage_id", ""))
    if storage_id != "mcap" and not allow_non_mcap_bag:
        raise RouteTeachingError("fixed-route teaching requires MCAP storage")
    bag_dir = source / "bag" / run_id
    bag_files = _bag_inventory(bag_dir, storage_id)
    fast_lio_path = source / "config/fast_lio.yaml"
    if fast_lio_path.is_symlink() or not fast_lio_path.is_file():
        raise RouteTeachingError("teaching run is missing config/fast_lio.yaml")
    pose_topic = str(localization.get("input_odom_topic", ""))
    try:
        poses, _speeds = _read_odometry_samples(
            bag_dir,
            pose_topic=pose_topic,
            speed_topic=None,
            odom_frame=str(localization.get("odom_frame", "")),
            base_frame=str(localization.get("base_frame", "")),
            storage_id=storage_id,
            max_pose_samples=settings["max_source_samples"],
        )
    except TrajectoryError as exc:
        raise RouteTeachingError(f"cannot read teaching odometry: {exc}") from exc
    source_samples, motion_report = _planar_samples(
        poses,
        base_to_body=base_to_body,
        input_global_frame=str(localization.get("input_global_frame", "")),
        body_frame=str(localization.get("body_frame", "")),
        settings=settings,
    )
    retained = _deduplicate_samples(
        source_samples,
        epsilon_m=settings["duplicate_epsilon_m"],
        max_yaw_change_rad=settings["max_duplicate_yaw_change_rad"],
    )
    snaps = _snap_stations(
        retained,
        stations,
        maximum_distance_m=settings["max_station_snap_distance_m"],
    )
    route, station_reports, segment_reports = _build_candidate(
        route_id=spec["route_id"],
        run_id=run_id,
        approved_speed=spec["approved_max_linear_mps"],
        samples=retained,
        snaps=snaps,
        settings=settings,
    )
    route_bytes = _yaml_bytes(route)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise RouteTeachingError("candidate output parent must not be a symbolic link")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent)
    )
    try:
        route_path = temporary / ROUTE_FILENAME
        route_path.write_bytes(route_bytes)
        try:
            candidate = load_fixed_route(route_path, allow_unverified=True)
        except FixedRouteError as exc:
            raise RouteTeachingError(f"generated route failed its contract: {exc}") from exc
        if candidate.verified or candidate.source_kind != "odometry_teaching_candidate":
            raise RouteTeachingError("generated route is not a safe teaching candidate")
        (temporary / CONFIG_FILENAME).write_bytes(config_bytes)
        (temporary / SPEC_FILENAME).write_bytes(spec_bytes)
        system_path = source / "config/system.yaml"
        manifest_path = source / "manifest.json"
        source_length = sum(
            math.hypot(current.x_m - previous.x_m, current.y_m - previous.y_m)
            for previous, current in zip(retained, retained[1:])
        )
        output_distances = [
            math.hypot(current.x_m - previous.x_m, current.y_m - previous.y_m)
            for previous, current in zip(candidate.poses, candidate.poses[1:])
        ]
        provenance = {
            "schema_version": "1.0",
            "route_id": candidate.route_id,
            "candidate": {
                "path": ROUTE_FILENAME,
                "sha256": _sha256(route_path),
                "source_kind": candidate.source_kind,
                "verified": candidate.verified,
                "frame_id": candidate.frame_id,
                "approved_max_linear_mps": candidate.approved_max_linear_mps,
                "pose_count": len(candidate.poses),
                "station_count": len(candidate.stations),
                "route_length_m": route_pose_length(candidate.poses),
                "spacing_m": {
                    "configured": settings["resample_spacing_m"],
                    "minimum": min(output_distances),
                    "maximum": max(output_distances),
                    "mean": sum(output_distances) / len(output_distances),
                },
            },
            "source": {
                "run_id": run_id,
                "run_status": operational["status"],
                "manifest_sha256": _sha256(manifest_path),
                "system_config_sha256": _sha256(system_path),
                "fast_lio_config_sha256": _sha256(fast_lio_path),
                "bag_storage_id": storage_id,
                "bag_files": bag_files,
                "topic": pose_topic,
                "type": SOURCE_TYPE,
                "parent_frame": str(localization.get("input_global_frame", "")),
                "child_frame": str(localization.get("body_frame", "")),
                "map_frame": str(localization.get("map_frame", "")),
                "base_frame": str(localization.get("base_frame", "")),
                "first_header_stamp_ns": source_samples[0].stamp_ns,
                "last_header_stamp_ns": source_samples[-1].stamp_ns,
                "read_pose_count": len(source_samples),
                "retained_pose_count": len(retained),
                "retained_length_m": source_length,
            },
            "extrinsics": {
                "verified": bringup.get("extrinsics_verified") is True,
                "base_to_body": {
                    "translation_m": list(base_to_body.translation),
                    "rotation_xyzw": list(base_to_body.rotation),
                },
            },
            "processing": {
                "backend": "Shapely Douglas-Peucker + NumPy arc interpolation",
                "config_path": CONFIG_FILENAME,
                "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
                "settings": settings,
                "motion": motion_report,
                "segments": segment_reports,
            },
            "teaching_spec": {
                "path": SPEC_FILENAME,
                "sha256": hashlib.sha256(spec_bytes).hexdigest(),
                "stations": station_reports,
            },
            "exporter": {
                "package": "inspection_pipeline",
                "version": "0.1.0",
                "git_commit": _git_commit(),
                "python": platform.python_version(),
                "rosbag2_py_path": str(Path(rosbag2_py.__file__).resolve()),
                "numpy": np.__version__,
                "numpy_path": str(Path(np.__file__).resolve()),
                "scipy": scipy.__version__,
                "scipy_path": str(Path(scipy.__file__).resolve()),
                "shapely": shapely.__version__,
                "shapely_path": str(Path(shapely.__file__).resolve()),
                "geos": geos.geos_version_string,
                "pyyaml": yaml.__version__,
                "pyyaml_path": str(Path(yaml.__file__).resolve()),
            },
            "review_required": [
                "overlay raw and candidate paths and inspect every corner",
                "verify each station position and final yaw onsite",
                "run supervised tracking at no more than 0.2 m/s",
                "archive acceptance evidence before promoting source_kind",
            ],
        }
        (temporary / PROVENANCE_FILENAME).write_bytes(_json_bytes(provenance))
        _fsync_tree(temporary)
        if target.exists():
            if _same_tree(target, temporary):
                shutil.rmtree(temporary)
                return target / ROUTE_FILENAME
            raise RouteTeachingError(
                f"candidate output already exists with different content: {target}"
            )
        os.replace(temporary, target)
        descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return target / ROUTE_FILENAME
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("teaching_spec", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--allow-non-mcap-bag", action="store_true")
    arguments = parser.parse_args(argv)
    output = export_route_candidate(
        arguments.run_dir,
        arguments.teaching_spec,
        arguments.output_directory,
        teaching_config_path=arguments.config,
        allow_non_mcap_bag=arguments.allow_non_mcap_bag,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
