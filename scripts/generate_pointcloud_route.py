#!/usr/bin/env python3
"""从 LIRIS 原始 PLY 自动识别主廊道并生成可复现的估计中心线。

这不是自动驾驶可通行性规划，也不是测量/验线成果。算法穷举水平朝向与偏移，
确定主廊道候选，再以稳健分位数估计横向与高程中心并检查逐段点云连续性。

默认只输出摘要；显式传入 --write 才会同步演示元数据、轨迹和空间标注。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets" / "tunnel" / "liris"
PLY_PATH = ASSET_DIR / "tunnel_pointcloud.ply"
ENGINE_METADATA_PATH = ASSET_DIR / "scene-metadata.json"
DEMO_DIR = ROOT / "data" / "demo"

ALGORITHM_VERSION = "2.0.0"
ANGLE_STEP_DEG = 5
LONGITUDINAL_BIN_M = 4.0
LATERAL_BIN_M = 2.0
VERTICAL_BIN_M = 1.0
CORRIDOR_OFFSET_STEP_M = 4.0
COARSE_HALF_WIDTH_M = 13.0
CROSS_SECTION_HALF_LENGTH_M = 4.5
CROSS_SECTION_HALF_WIDTH_M = 20.0
CONTINUITY_HALF_LENGTH_M = 2.0
MIN_CONTINUITY_SECTION_POINTS = 40
ROUTE_STATION_SPACING_M = 8.0
MIN_CROSS_SECTION_POINTS = 100
MIN_TUNNEL_WIDTH_M = 8.0
MAX_TUNNEL_WIDTH_M = 22.0
MAX_CONTINUITY_WIDTH_M = MAX_TUNNEL_WIDTH_M
MIN_TUNNEL_HEIGHT_M = 6.0
MIN_BIDIRECTIONAL_SUPPORT_M = 1.5
MAX_COARSE_GAP_STATIONS = 1
TURN_SEARCH_STEP_DEG = 15
MAX_TURN_DEG = 90
MAX_EXTENSION_STEPS = 64


class Point3D(TypedDict):
    x: float
    y: float
    z: float


class CorridorSelection(TypedDict):
    angle_deg: int
    offset_m: float
    station_start_m: float
    station_end_m: float


class CrossSectionEstimate(TypedDict):
    lateral_center_m: float
    vertical_center_m: float
    width_m: float
    height_m: float
    point_count: int


def point_distance(left: Point3D, right: Point3D) -> float:
    """按坐标轴名称计算距离，避免依赖字典字段的插入顺序。"""
    return math.dist(
        (left["x"], left["y"], left["z"]),
        (right["x"], right["y"], right["z"]),
    )


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_ascii_ply_xyz(path: Path) -> tuple[list[tuple[float, float, float]], int]:
    points: list[tuple[float, float, float]] = []
    vertex_count = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.startswith("element vertex "):
                vertex_count = int(line.split()[2])
            if line.strip() == "end_header":
                break
        for line in stream:
            columns = line.split()
            if len(columns) >= 3:
                points.append((float(columns[0]), float(columns[1]), float(columns[2])))
    if len(points) != vertex_count:
        raise ValueError(f"PLY 顶点数不一致：header={vertex_count}, parsed={len(points)}")
    return points, vertex_count


def transform_points(
    points: Iterable[tuple[float, float, float]], matrix: Sequence[float]
) -> list[tuple[float, float, float]]:
    if len(matrix) != 16:
        raise ValueError("transformMatrix 必须是 row-major 4x4")
    result = []
    for x, y, z in points:
        result.append(
            (
                matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
                matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
                matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
            )
        )
    return result


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def interpolate_missing(values: list[float | None]) -> list[float]:
    valid = [index for index, value in enumerate(values) if value is not None]
    if len(valid) < 2:
        raise ValueError("有效横断面不足，无法生成中心线")
    result = [0.0] * len(values)
    for index, value in enumerate(values):
        if value is not None:
            result[index] = value
            continue
        left = max((item for item in valid if item < index), default=valid[0])
        right = min((item for item in valid if item > index), default=valid[-1])
        if left == right:
            result[index] = float(values[left])
        else:
            ratio = (index - left) / (right - left)
            result[index] = float(values[left]) * (1 - ratio) + float(values[right]) * ratio
    return result


def smooth(values: list[float], passes: int = 2) -> list[float]:
    weights = (1, 2, 3, 2, 1)
    result = values[:]
    for _ in range(passes):
        next_values = result[:]
        for index in range(2, len(result) - 2):
            next_values[index] = sum(
                result[index + offset] * weight
                for offset, weight in zip(range(-2, 3), weights, strict=True)
            ) / sum(weights)
        result = next_values
    return result


def weighted_bin_quantile(
    bins: list[tuple[int, int]], probability: float, bin_size: float
) -> float:
    total = sum(count for _, count in bins)
    target = total * probability
    cumulative = 0
    for bin_index, count in sorted(bins):
        cumulative += count
        if cumulative >= target:
            return (bin_index + 0.5) * bin_size
    return (bins[-1][0] + 0.5) * bin_size


def select_main_corridor(
    points: Sequence[tuple[float, float, float]],
) -> CorridorSelection:
    """穷举方向与横向偏移，选择最长的直向隧道状横断面候选序列。"""
    if not points:
        raise ValueError("点云为空，无法识别巡检廊道")

    best_key: tuple[int, int, int, int] | None = None
    best: CorridorSelection | None = None
    lateral_radius_bins = round(COARSE_HALF_WIDTH_M / LATERAL_BIN_M)
    offset_step_bins = round(CORRIDOR_OFFSET_STEP_M / LATERAL_BIN_M)

    for angle_deg in range(0, 180, ANGLE_STEP_DEG):
        angle = math.radians(angle_deg)
        tx, tz = math.cos(angle), math.sin(angle)
        nx, nz = -tz, tx
        cells: dict[tuple[int, int], tuple[int, dict[int, int]]] = {}
        station_min = math.inf
        station_max = -math.inf
        lateral_min = math.inf
        lateral_max = -math.inf

        for x, y, z in points:
            station_bin = math.floor((x * tx + z * tz) / LONGITUDINAL_BIN_M)
            lateral_bin = math.floor((x * nx + z * nz) / LATERAL_BIN_M)
            vertical_bin = math.floor(y / VERTICAL_BIN_M)
            key = (station_bin, lateral_bin)
            if key in cells:
                count, vertical_counts = cells[key]
                vertical_counts[vertical_bin] = vertical_counts.get(vertical_bin, 0) + 1
                cells[key] = (count + 1, vertical_counts)
            else:
                cells[key] = (1, {vertical_bin: 1})
            station_min = min(station_min, station_bin)
            station_max = max(station_max, station_bin)
            lateral_min = min(lateral_min, lateral_bin)
            lateral_max = max(lateral_max, lateral_bin)

        offset_start_raw = int(lateral_min) + lateral_radius_bins
        offset_start = (
            math.ceil(offset_start_raw / offset_step_bins) * offset_step_bins
        )
        offset_stop = int(lateral_max) - lateral_radius_bins
        for offset_bin in range(offset_start, offset_stop + 1, offset_step_bins):
            valid: list[bool] = []
            station_bins = range(int(station_min), int(station_max) + 1)
            for station_bin in station_bins:
                lateral_counts: list[tuple[int, int]] = []
                vertical_counts: list[tuple[int, int]] = []
                point_count = 0
                for nearby_station in (station_bin - 1, station_bin, station_bin + 1):
                    for lateral_bin in range(
                        offset_bin - lateral_radius_bins,
                        offset_bin + lateral_radius_bins + 1,
                    ):
                        cell = cells.get((nearby_station, lateral_bin))
                        if cell is None:
                            continue
                        count, cell_vertical_counts = cell
                        lateral_counts.append((lateral_bin, count))
                        vertical_counts.extend(cell_vertical_counts.items())
                        point_count += count

                if point_count < MIN_CROSS_SECTION_POINTS or not lateral_counts:
                    valid.append(False)
                    continue
                low = weighted_bin_quantile(lateral_counts, 0.04, LATERAL_BIN_M)
                high = weighted_bin_quantile(lateral_counts, 0.96, LATERAL_BIN_M)
                floor = weighted_bin_quantile(vertical_counts, 0.04, VERTICAL_BIN_M)
                ceiling = weighted_bin_quantile(vertical_counts, 0.96, VERTICAL_BIN_M)
                width = high - low
                valid.append(
                    MIN_TUNNEL_WIDTH_M <= width <= MAX_TUNNEL_WIDTH_M
                    and ceiling - floor >= MIN_TUNNEL_HEIGHT_M
                )

            start_index = 0
            gap_count = 0
            for end_index, end_valid in enumerate(valid):
                if not end_valid:
                    gap_count += 1
                while gap_count > MAX_COARSE_GAP_STATIONS:
                    if not valid[start_index]:
                        gap_count -= 1
                    start_index += 1
                while start_index <= end_index and not valid[start_index]:
                    gap_count -= 1
                    start_index += 1
                if not end_valid or start_index > end_index:
                    continue
                span_count = end_index - start_index + 1
                valid_count = span_count - gap_count
                if valid_count < 4:
                    continue
                # 前两项决定最长连续证据；后两项只提供稳定、可复现的平局顺序。
                candidate_key = (valid_count, span_count, angle_deg, offset_bin)
                if best_key is not None and candidate_key <= best_key:
                    continue
                best_key = candidate_key
                first_station_bin = int(station_min) + start_index
                last_station_bin = int(station_min) + end_index + 1
                best = {
                    "angle_deg": angle_deg,
                    "offset_m": offset_bin * LATERAL_BIN_M,
                    "station_start_m": first_station_bin * LONGITUDINAL_BIN_M,
                    "station_end_m": last_station_bin * LONGITUDINAL_BIN_M,
                }

    if best is None:
        raise ValueError("未识别到连续的隧道状廊道")
    return best


def estimate_cross_section(
    points: Sequence[tuple[float, float, float]],
    guide_x: float,
    guide_z: float,
    tx: float,
    tz: float,
    *,
    half_length_m: float = CROSS_SECTION_HALF_LENGTH_M,
    min_points: int = MIN_CROSS_SECTION_POINTS,
) -> CrossSectionEstimate | None:
    nx, nz = -tz, tx
    longitudinal_values: list[float] = []
    lateral_values: list[float] = []
    vertical_values: list[float] = []
    for x, y, z in points:
        offset_x, offset_z = x - guide_x, z - guide_z
        longitudinal = offset_x * tx + offset_z * tz
        lateral = offset_x * nx + offset_z * nz
        if (
            abs(longitudinal) < half_length_m
            and abs(lateral) < CROSS_SECTION_HALF_WIDTH_M
        ):
            longitudinal_values.append(longitudinal)
            lateral_values.append(lateral)
            vertical_values.append(y)

    if len(lateral_values) < min_points:
        return None
    low = quantile(lateral_values, 0.04)
    high = quantile(lateral_values, 0.96)
    floor = quantile(vertical_values, 0.04)
    ceiling = quantile(vertical_values, 0.96)
    longitudinal_low = quantile(longitudinal_values, 0.1)
    longitudinal_high = quantile(longitudinal_values, 0.9)
    width = high - low
    height = ceiling - floor
    required_longitudinal_support = min(
        MIN_BIDIRECTIONAL_SUPPORT_M, half_length_m * 0.5
    )
    if not (
        MIN_TUNNEL_WIDTH_M <= width <= MAX_TUNNEL_WIDTH_M
        and height >= MIN_TUNNEL_HEIGHT_M
        and longitudinal_low <= -required_longitudinal_support
        and longitudinal_high >= required_longitudinal_support
    ):
        return None
    return {
        "lateral_center_m": (low + high) / 2,
        "vertical_center_m": (floor + ceiling) / 2,
        "width_m": width,
        "height_m": height,
        "point_count": len(lateral_values),
    }


def segment_has_continuous_support(
    points: Sequence[tuple[float, float, float]],
    start: Point3D,
    end: Point3D,
) -> bool:
    """验证三处隧道状支撑，拒绝跨空、明显偏移或合并过宽的拼接候选。"""
    dx = end["x"] - start["x"]
    dz = end["z"] - start["z"]
    horizontal_distance = math.hypot(dx, dz)
    spatial_distance = point_distance(start, end)
    if horizontal_distance == 0 or not (
        ROUTE_STATION_SPACING_M * 0.4
        <= spatial_distance
        <= ROUTE_STATION_SPACING_M * 1.65
    ):
        return False
    tx, tz = dx / horizontal_distance, dz / horizontal_distance
    nx, nz = -tz, tx
    for fraction in (0.25, 0.5, 0.75):
        guide_x = start["x"] + dx * fraction
        guide_z = start["z"] + dz * fraction
        lateral_values: list[float] = []
        vertical_values: list[float] = []
        for x, y, z in points:
            offset_x, offset_z = x - guide_x, z - guide_z
            longitudinal = offset_x * tx + offset_z * tz
            lateral = offset_x * nx + offset_z * nz
            if (
                abs(longitudinal) < CONTINUITY_HALF_LENGTH_M
                and abs(lateral) < CROSS_SECTION_HALF_WIDTH_M
            ):
                lateral_values.append(lateral)
                vertical_values.append(y)
        if len(lateral_values) < MIN_CONTINUITY_SECTION_POINTS:
            return False
        low = quantile(lateral_values, 0.04)
        high = quantile(lateral_values, 0.96)
        floor = quantile(vertical_values, 0.04)
        ceiling = quantile(vertical_values, 0.96)
        minimum_side_clearance = MIN_TUNNEL_WIDTH_M / 2
        if not (
            MIN_TUNNEL_WIDTH_M <= high - low <= MAX_CONTINUITY_WIDTH_M
            and low <= -minimum_side_clearance
            and high >= minimum_side_clearance
            and ceiling - floor >= MIN_TUNNEL_HEIGHT_M
        ):
            return False
        expected_y = start["y"] + (end["y"] - start["y"]) * fraction
        vertical_center = (floor + ceiling) / 2
        if abs(vertical_center - expected_y) > max(3.0, (ceiling - floor) * 0.4):
            return False
    return True


def longest_continuously_supported_route(
    points: Sequence[tuple[float, float, float]], route: Sequence[Point3D]
) -> list[Point3D]:
    """按连续点云证据切断粗搜索可能跨越的空白，并保留最长子段。"""
    segments: list[list[Point3D]] = [[dict(route[0])]]
    for start, end in zip(route, route[1:], strict=False):
        if not segment_has_continuous_support(points, start, end):
            segments.append([])
        segments[-1].append(dict(end))
    supported = [segment for segment in segments if len(segment) >= 2]
    if not supported:
        raise ValueError("未识别到具有连续点云支撑的中心线路段")
    return max(supported, key=route_length)


def extend_route_end(
    points: Sequence[tuple[float, float, float]], route: Sequence[Point3D]
) -> list[Point3D]:
    """从路线末端局部搜索后续横断面，允许连续廊道逐站转弯。"""
    result = [dict(point) for point in route]
    for _ in range(MAX_EXTENSION_STEPS):
        previous, current = result[-2], result[-1]
        heading = math.atan2(
            current["z"] - previous["z"], current["x"] - previous["x"]
        )
        candidates: list[tuple[tuple[float, float, int], Point3D]] = []
        for turn_deg in range(-MAX_TURN_DEG, MAX_TURN_DEG + 1, TURN_SEARCH_STEP_DEG):
            candidate_heading = heading + math.radians(turn_deg)
            tx, tz = math.cos(candidate_heading), math.sin(candidate_heading)
            nx, nz = -tz, tx
            guide_x = current["x"] + ROUTE_STATION_SPACING_M * tx
            guide_z = current["z"] + ROUTE_STATION_SPACING_M * tz
            estimate = estimate_cross_section(points, guide_x, guide_z, tx, tz)
            if estimate is None:
                continue
            candidate: Point3D = {
                "x": round(guide_x + estimate["lateral_center_m"] * nx, 3),
                "y": round(estimate["vertical_center_m"], 3),
                "z": round(guide_z + estimate["lateral_center_m"] * nz, 3),
            }
            dx = candidate["x"] - current["x"]
            dz = candidate["z"] - current["z"]
            horizontal_distance = math.hypot(dx, dz)
            spatial_distance = point_distance(current, candidate)
            if not (
                ROUTE_STATION_SPACING_M * 0.45
                <= spatial_distance
                <= ROUTE_STATION_SPACING_M * 1.65
            ):
                continue
            if dx * tx + dz * tz < ROUTE_STATION_SPACING_M * 0.25:
                continue
            if any(
                point_distance(candidate, prior) < ROUTE_STATION_SPACING_M * 0.95
                for prior in result[:-2]
            ):
                continue
            actual_heading = math.atan2(dz, dx)
            actual_turn = abs(
                math.atan2(
                    math.sin(actual_heading - heading),
                    math.cos(actual_heading - heading),
                )
            )
            if actual_turn > math.radians(MAX_TURN_DEG):
                continue
            score = (
                actual_turn,
                abs(spatial_distance - ROUTE_STATION_SPACING_M),
                -estimate["point_count"],
            )
            candidates.append((score, candidate))

        if not candidates:
            break
        selected = next(
            (
                candidate
                for _, candidate in sorted(candidates, key=lambda item: item[0])
                if segment_has_continuous_support(points, current, candidate)
            ),
            None,
        )
        if selected is None:
            break
        result.append(selected)
    return result


def derive_route(
    points: Sequence[tuple[float, float, float]],
) -> list[Point3D]:
    selection = select_main_corridor(points)
    angle = math.radians(selection["angle_deg"])
    tx, tz = math.cos(angle), math.sin(angle)
    nx, nz = -tz, tx
    corridor_length = selection["station_end_m"] - selection["station_start_m"]
    station_count = max(3, round(corridor_length / ROUTE_STATION_SPACING_M) + 1)
    stations = [
        selection["station_start_m"]
        + corridor_length * index / (station_count - 1)
        for index in range(station_count)
    ]
    guides = [
        (
            station * tx + selection["offset_m"] * nx,
            station * tz + selection["offset_m"] * nz,
        )
        for station in stations
    ]

    lateral_centers: list[float | None] = []
    vertical_centers: list[float | None] = []
    valid_indices: list[int] = []
    for index, guide in enumerate(guides):
        estimate = estimate_cross_section(points, guide[0], guide[1], tx, tz)
        if estimate is None:
            lateral_centers.append(None)
            vertical_centers.append(None)
            continue
        lateral_centers.append(estimate["lateral_center_m"])
        vertical_centers.append(estimate["vertical_center_m"])
        valid_indices.append(index)

    lateral = smooth(interpolate_missing(lateral_centers))
    vertical = smooth(interpolate_missing(vertical_centers))
    first_valid, last_valid = valid_indices[0], valid_indices[-1]
    route = []
    for index in range(first_valid, last_valid + 1):
        guide = guides[index]
        route.append(
            {
                "x": round(guide[0] + lateral[index] * nx, 3),
                "y": round(vertical[index], 3),
                "z": round(guide[1] + lateral[index] * nz, 3),
            }
        )
    route = longest_continuously_supported_route(points, route)
    forward = extend_route_end(points, route)
    backward = extend_route_end(points, list(reversed(route)))
    route = list(reversed(backward[len(route) :])) + forward
    if route[0]["x"] > route[-1]["x"] or (
        route[0]["x"] == route[-1]["x"] and route[0]["z"] > route[-1]["z"]
    ):
        route.reverse()
    return route


def route_length(route: Sequence[Point3D]) -> float:
    return sum(
        point_distance(left, right)
        for left, right in zip(route, route[1:], strict=False)
    )


def sample_route(
    route: Sequence[Point3D], count: int
) -> list[tuple[Point3D, tuple[float, float, float]]]:
    lengths = [
        point_distance(left, right)
        for left, right in zip(route, route[1:], strict=False)
    ]
    total = sum(lengths)
    samples = []
    for sample_index in range(count):
        target = total * sample_index / max(count - 1, 1)
        traversed = 0.0
        for index, segment_length in enumerate(lengths):
            if target <= traversed + segment_length or index == len(lengths) - 1:
                ratio = 0.0 if segment_length == 0 else (target - traversed) / segment_length
                left, right = route[index], route[index + 1]
                point = {
                    axis: round(left[axis] + (right[axis] - left[axis]) * ratio, 3)
                    for axis in ("x", "y", "z")
                }
                tangent = tuple(right[axis] - left[axis] for axis in ("x", "y", "z"))
                samples.append((point, tangent))
                break
            traversed += segment_length
    return samples


def offset_from_route(
    route: Sequence[Point3D], fraction: float, lateral: float, vertical: float
) -> Point3D:
    point, tangent = sample_route(route, 101)[round(fraction * 100)]
    horizontal = math.hypot(tangent[0], tangent[2]) or 1.0
    nx, nz = -tangent[2] / horizontal, tangent[0] / horizontal
    return {
        "x": round(point["x"] + nx * lateral, 3),
        "y": round(point["y"] + vertical, 3),
        "z": round(point["z"] + nz * lateral, 3),
    }


def sync_demo_files(route: list[Point3D], provenance: dict) -> None:
    length = route_length(route)
    engine = load_json(ENGINE_METADATA_PATH)
    engine["recommendedRoute"] = route
    engine["routeGeneration"] = {
        "method": provenance["method"],
        "algorithmVersion": provenance["algorithm_version"],
        "sourceAsset": provenance["source_asset"],
        "sourceSha256": provenance["source_sha256"],
        "sourcePointCount": provenance["source_point_count"],
        "generatedPointCount": provenance["generated_point_count"],
        "status": provenance["status"],
        "disclaimer": provenance["disclaimer"],
    }
    anchor_fractions = (0.08, 0.2, 0.32, 0.45, 0.58, 0.7, 0.82, 0.94)
    engine["recommendedDeviceAnchors"] = [
        {
            "id": f"derived-anchor-{index:02d}",
            "label": f"Point-cloud-aligned demo anchor {index}",
            "position": offset_from_route(route, fraction, -4.8 if index % 2 else 4.8, 1.8),
            "note": "Simulated device anchor aligned to the estimated route; not a real sensor.",
        }
        for index, fraction in enumerate(anchor_fractions, start=1)
    ]
    dump_json(ENGINE_METADATA_PATH, engine)

    metadata = load_json(DEMO_DIR / "scene-metadata.json")
    active = next(item for item in metadata if item["scene_id"] == "scene-001")
    active["length_m"] = round(length)
    active["route"] = route
    active["route_provenance"] = provenance
    active["description"] = (
        "CNRS LIRIS Synthetic Tunnel (sub_2)；几何来自公开 OBJ/PLY，"
        "巡检路线由 PLY 自动主廊道横断面估计；业务对象为模拟数据"
    )
    dump_json(DEMO_DIR / "scene-metadata.json", metadata)

    scenes = load_json(DEMO_DIR / "scenes.json")
    scene = next(item for item in scenes if item["id"] == "scene-001")
    scene["length_m"] = round(length)
    scene["description"] = (
        "LIRIS 公开几何上的隧道巡检功能原型；路线为点云自动主廊道算法估计，"
        "业务数据为模拟"
    )
    dump_json(DEMO_DIR / "scenes.json", scenes)

    tasks = load_json(DEMO_DIR / "tasks.json")
    replay = next(item for item in tasks if item["id"] == "task-replay-001")
    replay["distance_m"] = round(length, 1)
    dump_json(DEMO_DIR / "tasks.json", tasks)

    trajectory_samples = sample_route(route, 157)
    trajectory = []
    base_seconds = 9 * 3600 + 12
    speed = round(length / 156, 3)
    for index, (point, tangent) in enumerate(trajectory_samples):
        total_seconds = base_seconds + index
        heading = math.degrees(math.atan2(tangent[2], tangent[0])) % 360
        timestamp = (
            f"2026-08-01T{total_seconds // 3600:02d}:"
            f"{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}Z"
        )
        trajectory.append(
            {
                "seq": index,
                "timestamp": timestamp,
                "position": point,
                "heading_deg": round(heading, 1),
                "speed_mps": speed,
            }
        )
    dump_json(DEMO_DIR / "trajectories.json", {"task-replay-001": trajectory})

    devices = load_json(DEMO_DIR / "devices.json")
    for index, device in enumerate(devices):
        fraction = anchor_fractions[index % len(anchor_fractions)]
        device["position"] = offset_from_route(
            route, fraction, -4.8 if index % 2 == 0 else 4.8, 1.8
        )
    dump_json(DEMO_DIR / "devices.json", devices)

    events = load_json(DEMO_DIR / "events.json")
    event_fractions = (0.16, 0.36, 0.56, 0.75, 0.9)
    for index, event in enumerate(events):
        event["position"] = offset_from_route(
            route,
            event_fractions[index % len(event_fractions)],
            -5.2 if index % 2 == 0 else 5.2,
            0.8,
        )
    dump_json(DEMO_DIR / "events.json", events)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="同步更新仓库内演示数据")
    args = parser.parse_args()

    engine = load_json(ENGINE_METADATA_PATH)
    points, point_count = read_ascii_ply_xyz(PLY_PATH)
    transformed = transform_points(points, engine["transformMatrix"])
    route = derive_route(transformed)
    actual_hash = sha256(PLY_PATH)
    expected_hash = engine["source"]["sha256"][PLY_PATH.name]
    if actual_hash != expected_hash:
        raise ValueError(f"PLY SHA-256 不匹配：expected={expected_hash}, actual={actual_hash}")
    provenance = {
        "method": "pointcloud_auto_corridor_centerline",
        "algorithm_version": ALGORITHM_VERSION,
        "source_asset": PLY_PATH.name,
        "source_sha256": actual_hash,
        "source_point_count": point_count,
        "generated_point_count": len(route),
        "status": "estimated",
        "disclaimer": (
            "点云自动主廊道横断面算法估计，非可通行规划、非实测路线；"
            "投用前须用甲方 GIS/BIM 与现场验线确认"
        ),
    }
    if args.write:
        sync_demo_files(route, provenance)
    print(
        json.dumps(
            {
                "written": args.write,
                "route_points": len(route),
                "route_length_m": round(route_length(route), 3),
                "source_points": point_count,
                "source_sha256": actual_hash,
                "status": provenance["status"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
