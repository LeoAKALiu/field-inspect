"""Deterministic Display Trajectory reduction of a Replay Trajectory."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

ALGORITHM = "endpoint_landmark_stride"
ALGORITHM_VERSION = "1.0"
DEFAULT_MAX_POINTS = 500
DISCLAIMER = (
    "Display Trajectory is a deterministic reduction of the Replay Trajectory; "
    "it is not original evidence and is not a live 1,000,000-point render promise."
)


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.dist(a, b)


def build_display_trajectory(
    points: Sequence[dict[str, Any]],
    landmarks: Sequence[dict[str, Any]] | None = None,
    *,
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict[str, Any]:
    """Keep first, last, and nearest-to-each-landmark points, then stride the rest."""
    if not points:
        return {
            "algorithm": ALGORITHM,
            "algorithm_version": ALGORITHM_VERSION,
            "source_point_count": 0,
            "display_point_count": 0,
            "kept_seq": [],
            "landmark_seqs": {},
            "disclaimer": DISCLAIMER,
            "points": [],
        }
    keep: set[int] = {0, len(points) - 1}
    landmark_seqs: dict[str, int] = {}
    for landmark in landmarks or []:
        target = (
            float(landmark["x"]),
            float(landmark["y"]),
            float(landmark["z"]),
        )
        best_i = min(
            range(len(points)),
            key=lambda i: _dist(
                (
                    float(points[i]["position"]["x"]),
                    float(points[i]["position"]["y"]),
                    float(points[i]["position"]["z"]),
                ),
                target,
            ),
        )
        keep.add(best_i)
        landmark_seqs[str(landmark["landmark_id"])] = int(points[best_i]["seq"])

    remaining = max(max_points, len(keep)) - len(keep)
    if remaining > 0 and len(points) > len(keep):
        stride = max(1, math.floor(len(points) / (remaining + 1)))
        for index in range(0, len(points), stride):
            keep.add(index)
            if len(keep) >= max_points:
                break
    ordered = sorted(keep)
    if len(ordered) > max_points:
        head, tail = ordered[0], ordered[-1]
        middle = ordered[1:-1]
        landmark_indexes = {int(seq) for seq in landmark_seqs.values()}
        forced = [i for i in middle if points[i]["seq"] in landmark_indexes]
        fill = [i for i in middle if points[i]["seq"] not in landmark_indexes]
        budget = max_points - 2 - len(forced)
        ordered = [head, *forced, *fill[: max(budget, 0)], tail]
        ordered = sorted(set(ordered))
    display_points = [points[i] for i in ordered]
    return {
        "algorithm": ALGORITHM,
        "algorithm_version": ALGORITHM_VERSION,
        "source_point_count": len(points),
        "display_point_count": len(display_points),
        "kept_seq": [int(point["seq"]) for point in display_points],
        "landmark_seqs": landmark_seqs,
        "disclaimer": DISCLAIMER,
        "points": display_points,
    }
