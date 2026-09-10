"""Build smaller, display-only tunnel assets for bandwidth-limited AutoDL delivery.

The source LIRIS OBJ/PLY files are never modified. The generated mesh preserves the
overall geometry with fewer faces; the generated point cloud preserves sampled
positions and vertex colors while dropping normals, UVs, and redundant alpha data.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import trimesh
from trimesh.exchange.obj import export_obj
from trimesh.exchange.ply import export_ply


def optimize_mesh(source: Path, target: Path, face_count: int) -> tuple[int, int]:
    loaded = trimesh.load(source, force="mesh", process=False)
    if not isinstance(loaded, trimesh.Trimesh):
        raise TypeError(f"Expected a triangle mesh, got {type(loaded).__name__}")
    original_faces = len(loaded.faces)
    if original_faces <= face_count:
        optimized = loaded
    else:
        optimized = loaded.simplify_quadric_decimation(face_count=face_count)
    target.write_text(
        export_obj(
            optimized,
            include_normals=False,
            include_color=False,
            include_texture=False,
            digits=5,
        ),
        encoding="utf-8",
    )
    return original_faces, len(optimized.faces)


def optimize_point_cloud(
    source: Path, target: Path, point_count: int
) -> tuple[int, int]:
    loaded = trimesh.load(source, process=False)
    if not isinstance(loaded, trimesh.points.PointCloud):
        raise TypeError(f"Expected a point cloud, got {type(loaded).__name__}")

    vertices = np.asarray(loaded.vertices)
    colors = np.asarray(loaded.colors)
    original_points = len(vertices)
    if original_points > point_count:
        indices = np.linspace(0, original_points - 1, point_count, dtype=np.int64)
        vertices = vertices[indices]
        colors = colors[indices]

    optimized = trimesh.points.PointCloud(vertices=vertices, colors=colors)
    target.write_bytes(export_ply(optimized, encoding="binary_little_endian"))
    return original_points, len(vertices)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--mesh-faces", type=int, default=70_000)
    parser.add_argument("--point-count", type=int, default=60_000)
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    mesh_source = args.source_root / "tunnel_mesh.obj"
    points_source = args.source_root / "tunnel_pointcloud.ply"
    mesh_target = args.output_root / mesh_source.name
    points_target = args.output_root / points_source.name

    mesh_before, mesh_after = optimize_mesh(mesh_source, mesh_target, args.mesh_faces)
    points_before, points_after = optimize_point_cloud(
        points_source, points_target, args.point_count
    )
    print(
        f"mesh_faces={mesh_before}->{mesh_after} "
        f"mesh_bytes={mesh_source.stat().st_size}->{mesh_target.stat().st_size}"
    )
    print(
        f"points={points_before}->{points_after} "
        f"pointcloud_bytes={points_source.stat().st_size}->{points_target.stat().st_size}"
    )


if __name__ == "__main__":
    main()
