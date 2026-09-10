"""点云路线生成器的可复现性与真实性边界。"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_route_generator():
    module_path = REPO_ROOT / "scripts" / "generate_pointcloud_route.py"
    spec = importlib.util.spec_from_file_location("generate_pointcloud_route", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_tunnel(
    *,
    axis: str,
    center: tuple[float, float, float],
    length: int,
) -> list[tuple[float, float, float]]:
    """生成两壁、地面和拱顶组成的规则点云，仅用于公开生成器行为测试。"""
    points: list[tuple[float, float, float]] = []
    cx, cy, cz = center
    for station in range(-length // 2, length // 2 + 1):
        for vertical in range(-4, 5):
            for lateral in (-6, 6):
                if axis == "x":
                    points.append((cx + station, cy + vertical, cz + lateral))
                else:
                    points.append((cx + lateral, cy + vertical, cz + station))
        for lateral in range(-6, 7):
            for vertical in (-4, 4):
                if axis == "x":
                    points.append((cx + station, cy + vertical, cz + lateral))
                else:
                    points.append((cx + lateral, cy + vertical, cz + station))
    return points


def flat_strip_with_sparse_vertical_outliers(
    *, center: tuple[float, float, float], length: int
) -> list[tuple[float, float, float]]:
    """近似平面点带；每站仅一个高程离群点，不应构成隧道横断面。"""
    points: list[tuple[float, float, float]] = []
    cx, cy, cz = center
    for station in range(-length // 2, length // 2 + 1):
        for sample in range(100):
            lateral = -6.0 + 12.0 * sample / 99
            points.append((cx + station, cy, cz + lateral))
        points.append((cx + station, cy + 20.0, cz))
    return points


def test_route_generator_reproduces_committed_metadata_without_writing():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_pointcloud_route.py")],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(result.stdout)
    metadata = json.loads(
        (REPO_ROOT / "data" / "demo" / "scene-metadata.json").read_text(
            encoding="utf-8"
        )
    )[0]

    assert summary["written"] is False
    assert summary["route_points"] == len(metadata["route"]) == 20
    assert round(summary["route_length_m"]) == metadata["length_m"] == 152
    assert summary["source_points"] == 162594
    assert summary["source_sha256"] == metadata["route_provenance"]["source_sha256"]
    assert summary["status"] == metadata["route_provenance"]["status"] == "estimated"

    generator = load_route_generator()
    engine = generator.load_json(generator.ENGINE_METADATA_PATH)
    points, _ = generator.read_ascii_ply_xyz(generator.PLY_PATH)
    transformed = generator.transform_points(points, engine["transformMatrix"])
    regenerated = generator.derive_route(transformed)
    assert regenerated == metadata["route"]


def test_route_generator_selects_longest_corridor_without_manual_endpoints():
    generator = load_route_generator()
    points = synthetic_tunnel(axis="x", center=(10.0, 5.0, 20.0), length=120)
    points.extend(
        synthetic_tunnel(axis="z", center=(90.0, -10.0, -40.0), length=60)
    )

    route = generator.derive_route(points)

    assert generator.route_length(route) >= 105
    assert route[-1]["x"] - route[0]["x"] >= 105
    assert max(abs(point["z"] - 20.0) for point in route) <= 1.5
    assert max(abs(point["y"] - 5.0) for point in route) <= 1.5


def test_sparse_vertical_outliers_do_not_turn_a_flat_strip_into_a_corridor():
    generator = load_route_generator()
    points = synthetic_tunnel(axis="x", center=(0.0, 5.0, 0.0), length=100)
    points.extend(
        flat_strip_with_sparse_vertical_outliers(
            center=(0.0, -20.0, 70.0), length=150
        )
    )

    route = generator.derive_route(points)

    assert generator.route_length(route) >= 85
    assert max(abs(point["z"]) for point in route) <= 1.5
    assert max(abs(point["y"] - 5.0) for point in route) <= 1.5


def test_route_generator_follows_a_connected_right_angle_turn():
    generator = load_route_generator()
    points = synthetic_tunnel(axis="x", center=(-10.0, 5.0, 0.0), length=100)
    points.extend(
        synthetic_tunnel(axis="z", center=(40.0, 5.0, 40.0), length=80)
    )

    route = generator.derive_route(points)

    assert generator.route_length(route) >= 150
    assert min(point["x"] for point in route) <= -50
    assert max(point["z"] for point in route) >= 70
    assert max(abs(point["y"] - 5.0) for point in route) <= 1.5


def test_nearby_but_disconnected_corridors_are_not_stitched_together():
    generator = load_route_generator()
    points = synthetic_tunnel(axis="x", center=(-40.0, 5.0, 0.0), length=80)
    points.extend(
        synthetic_tunnel(axis="x", center=(28.0, 5.0, 10.0), length=40)
    )

    route = generator.derive_route(points)

    assert generator.route_length(route) >= 65
    assert max(abs(point["z"]) for point in route) <= 4
    assert max(point["x"] for point in route) <= 4


def test_parallel_corridor_offset_from_candidate_segment_is_not_support():
    generator = load_route_generator()
    points = synthetic_tunnel(axis="x", center=(0.0, 5.0, 10.0), length=80)

    supported = generator.segment_has_continuous_support(
        points,
        {"x": 0.0, "y": 5.0, "z": 0.0},
        {"x": 8.0, "y": 5.0, "z": 0.0},
    )

    assert supported is False


def test_two_parallel_corridors_do_not_masquerade_as_one_centered_corridor():
    generator = load_route_generator()
    points = synthetic_tunnel(axis="x", center=(0.0, 5.0, -8.0), length=80)
    points.extend(synthetic_tunnel(axis="x", center=(0.0, 5.0, 8.0), length=80))

    supported = generator.segment_has_continuous_support(
        points,
        {"x": 0.0, "y": 5.0, "z": 0.0},
        {"x": 8.0, "y": 5.0, "z": 0.0},
    )

    assert supported is False


def test_point_distance_is_independent_of_mapping_insertion_order():
    generator = load_route_generator()
    left = {"z": 0.0, "x": 0.0, "y": 0.0}
    right = {"y": 4.0, "z": 0.0, "x": 3.0}
    assert generator.point_distance(left, right) == 5.0
