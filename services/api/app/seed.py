"""启动种子：仅当库为空时从 data/demo/ 载入（幂等）。"""

from __future__ import annotations

import json
from pathlib import Path

from .db import Database


def _load(demo_dir: Path, name: str):
    with open(demo_dir / name, encoding="utf-8") as f:
        return json.load(f)


def seed_if_empty(db: Database, demo_dir: Path) -> bool:
    """库为空则载入种子数据，返回是否执行了载入。"""
    if not db.is_empty():
        return False

    for s in _load(demo_dir, "scenes.json"):
        db.execute(
            "INSERT INTO scenes (id, name, description, status, geometry_ref,"
            " bounds_min_x, bounds_min_y, bounds_min_z,"
            " bounds_max_x, bounds_max_y, bounds_max_z, length_m, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                s["id"], s["name"], s.get("description"), s["status"], s.get("geometry_ref"),
                s["bounds_min"]["x"], s["bounds_min"]["y"], s["bounds_min"]["z"],
                s["bounds_max"]["x"], s["bounds_max"]["y"], s["bounds_max"]["z"],
                s.get("length_m"), s["created_at"],
            ),
        )

    for t in _load(demo_dir, "tasks.json"):
        db.execute(
            "INSERT INTO tasks (id, scene_id, name, mode, status, planned_start,"
            " actual_start, actual_end, distance_m, event_count,"
            " run_kind, package_status, acceptance_state, scene_version_id, alignment_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                t["id"], t["scene_id"], t["name"], t["mode"], t["status"],
                t.get("planned_start"), t.get("actual_start"), t.get("actual_end"),
                t.get("distance_m"), t.get("event_count", 0),
                t.get("run_kind", "demonstration"), t.get("package_status"),
                t.get("acceptance_state", "not_applicable"),
                t.get("scene_version_id"), t.get("alignment_id"),
            ),
        )

    for task_id, points in _load(demo_dir, "trajectories.json").items():
        for p in points:
            db.execute(
                "INSERT INTO trajectory_points (task_id, seq, timestamp, x, y, z,"
                " heading_deg, speed_mps) VALUES (?,?,?,?,?,?,?,?)",
                (
                    task_id, p["seq"], p["timestamp"],
                    p["position"]["x"], p["position"]["y"], p["position"]["z"],
                    p.get("heading_deg"), p.get("speed_mps"),
                ),
            )

    for e in _load(demo_dir, "events.json"):
        db.execute(
            "INSERT INTO events (id, scene_id, task_id, type, severity, status,"
            " x, y, z, description, confidence, image_ref, detected_at,"
            " handled_by, handled_at, handle_comment)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                e["id"], e["scene_id"], e["task_id"], e["type"], e["severity"], e["status"],
                e["position"]["x"], e["position"]["y"], e["position"]["z"],
                e.get("description"), e.get("confidence"), e.get("image_ref"),
                e["detected_at"], e.get("handled_by"), e.get("handled_at"),
                e.get("handle_comment"),
            ),
        )

    for d in _load(demo_dir, "devices.json"):
        db.execute(
            "INSERT INTO devices (id, scene_id, name, type, unit, x, y, z, status)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                d["id"], d["scene_id"], d["name"], d["type"], d["unit"],
                d["position"]["x"], d["position"]["y"], d["position"]["z"], d["status"],
            ),
        )

    metadata_path = demo_dir / "scene-metadata.json"
    if metadata_path.exists():
        for item in _load(demo_dir, "scene-metadata.json"):
            db.execute(
                "INSERT INTO scene_metadata (scene_id, data) VALUES (?,?)",
                (item["scene_id"], json.dumps(item, ensure_ascii=False)),
            )

    return True
