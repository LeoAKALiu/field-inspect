"""SQLite 访问层（stdlib sqlite3，无 ORM）。建表幂等，行转契约 dict。"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .provenance import make_provenance

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL,
  geometry_ref TEXT,
  bounds_min_x REAL, bounds_min_y REAL, bounds_min_z REAL,
  bounds_max_x REAL, bounds_max_y REAL, bounds_max_z REAL,
  length_m REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  scene_id TEXT NOT NULL,
  name TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  planned_start TEXT,
  actual_start TEXT,
  actual_end TEXT,
  distance_m REAL,
  event_count INTEGER NOT NULL DEFAULT 0,
  run_kind TEXT NOT NULL DEFAULT 'demonstration',
  package_status TEXT,
  acceptance_state TEXT NOT NULL DEFAULT 'not_applicable',
  scene_version_id TEXT,
  alignment_id TEXT
);
CREATE TABLE IF NOT EXISTS trajectory_points (
  task_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  timestamp TEXT NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
  heading_deg REAL,
  speed_mps REAL,
  PRIMARY KEY (task_id, seq)
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  scene_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  type TEXT NOT NULL,
  severity TEXT NOT NULL,
  status TEXT NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
  description TEXT,
  confidence REAL,
  image_ref TEXT,
  detected_at TEXT NOT NULL,
  handled_by TEXT,
  handled_at TEXT,
  handle_comment TEXT
);
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,
  scene_id TEXT NOT NULL,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  unit TEXT NOT NULL,
  x REAL, y REAL, z REAL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scene_metadata (
  scene_id TEXT PRIMARY KEY,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_bundle_imports (
  run_id TEXT PRIMARY KEY,
  bundle_sha256 TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  archive_path TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  package_status TEXT,
  run_kind TEXT,
  acceptance_state TEXT,
  scene_version_id TEXT,
  alignment_id TEXT
);
CREATE TABLE IF NOT EXISTS scene_versions (
  scene_version_id TEXT PRIMARY KEY,
  scene_id TEXT NOT NULL,
  asset_sha256 TEXT NOT NULL,
  operator_label TEXT NOT NULL,
  registered_at TEXT NOT NULL,
  navigation_default INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS verified_alignments (
  alignment_id TEXT PRIMARY KEY,
  scene_version_id TEXT NOT NULL,
  evidence_sha256 TEXT NOT NULL,
  operator_label TEXT NOT NULL,
  registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS registered_landmarks (
  landmark_id TEXT NOT NULL,
  alignment_id TEXT NOT NULL,
  marker_id INTEGER NOT NULL,
  dictionary TEXT NOT NULL,
  role TEXT NOT NULL,
  physical_size_m REAL NOT NULL,
  pos_x REAL NOT NULL,
  pos_y REAL NOT NULL,
  pos_z REAL NOT NULL,
  roll_deg REAL NOT NULL,
  pitch_deg REAL NOT NULL,
  yaw_deg REAL NOT NULL,
  translation_m REAL NOT NULL,
  rotation_deg REAL NOT NULL,
  min_valid_samples INTEGER NOT NULL,
  route_portion TEXT,
  PRIMARY KEY (alignment_id, landmark_id)
);
CREATE TABLE IF NOT EXISTS import_rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  stage TEXT NOT NULL,
  message TEXT NOT NULL,
  run_id TEXT,
  package_sha256 TEXT,
  schema_version TEXT,
  source_kind TEXT,
  inbox_name TEXT,
  quarantine_path TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acceptance_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  operator_label TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  first_frame TEXT,
  last_frame TEXT,
  landmark_results_json TEXT,
  outcome TEXT NOT NULL,
  remarks TEXT
);
CREATE TABLE IF NOT EXISTS verified_backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  backup_root TEXT NOT NULL,
  device_id INTEGER NOT NULL,
  archive_sha256 TEXT NOT NULL,
  ledger_sha256 TEXT NOT NULL,
  scene_sha256 TEXT NOT NULL,
  alignment_sha256 TEXT NOT NULL,
  verified_at TEXT NOT NULL
);
"""

_TASK_COLUMNS = (
    ("run_kind", "TEXT NOT NULL DEFAULT 'demonstration'"),
    ("package_status", "TEXT"),
    ("acceptance_state", "TEXT NOT NULL DEFAULT 'not_applicable'"),
    ("scene_version_id", "TEXT"),
    ("alignment_id", "TEXT"),
    ("has_pointcloud", "INTEGER NOT NULL DEFAULT 0"),
    ("acceptance_recorded_at", "TEXT"),
)
_SCENE_VERSION_COLUMNS = (("navigation_default", "INTEGER NOT NULL DEFAULT 1"),)
_IMPORT_COLUMNS = (
    ("package_status", "TEXT"),
    ("run_kind", "TEXT"),
    ("acceptance_state", "TEXT"),
    ("scene_version_id", "TEXT"),
    ("alignment_id", "TEXT"),
)


def _ensure_columns(
    conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


class Database:
    """单连接 + 锁的薄封装；TestClient/uvicorn 跨线程访问安全。"""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            from .instruments import SCHEMA as INSTRUMENT_SCHEMA
            self._conn.executescript(INSTRUMENT_SCHEMA)
            from .processing_jobs import SCHEMA as PROCESSING_SCHEMA
            self._conn.executescript(PROCESSING_SCHEMA)
            _ensure_columns(self._conn, "tasks", _TASK_COLUMNS)
            _ensure_columns(self._conn, "run_bundle_imports", _IMPORT_COLUMNS)
            _ensure_columns(self._conn, "scene_versions", _SCENE_VERSION_COLUMNS)
            self._conn.commit()

    def is_empty(self) -> bool:
        row = self.query_one("SELECT COUNT(*) AS c FROM scenes")
        return row["c"] == 0

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a caller's writes atomically under the database's single-writer lock."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---- 行 → 契约对象 ----


def scene_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "status": row["status"],
        "geometry_ref": row["geometry_ref"],
        "bounds_min": {
            "x": row["bounds_min_x"],
            "y": row["bounds_min_y"],
            "z": row["bounds_min_z"],
        },
        "bounds_max": {
            "x": row["bounds_max_x"],
            "y": row["bounds_max_y"],
            "z": row["bounds_max_z"],
        },
        "length_m": row["length_m"],
        "created_at": row["created_at"],
        "source_type": "simulation",
        # 坐标系/几何格式/名称均待甲方确认
        "provenance": make_provenance("simulation", "pending_confirmation"),
    }


def _row_value(row: sqlite3.Row, key: str, default=None):
    return row[key] if key in row.keys() else default


def task_to_dict(row: sqlite3.Row) -> dict:
    run_kind = _row_value(row, "run_kind") or "demonstration"
    if run_kind == "recorded":
        source = "replay"
        provenance_status = "pending_confirmation"
    else:
        source = row["mode"] if row["mode"] in ("simulation", "replay") else "simulation"
        provenance_status = "simulated"
    return {
        "id": row["id"],
        "scene_id": row["scene_id"],
        "name": row["name"],
        "mode": row["mode"],
        "status": row["status"],
        "planned_start": row["planned_start"],
        "actual_start": row["actual_start"],
        "actual_end": row["actual_end"],
        "distance_m": row["distance_m"],
        "event_count": row["event_count"],
        "run_kind": run_kind,
        "package_status": _row_value(row, "package_status"),
        "acceptance_state": _row_value(row, "acceptance_state") or "not_applicable",
        "scene_version_id": _row_value(row, "scene_version_id"),
        "alignment_id": _row_value(row, "alignment_id"),
        "has_pointcloud": bool(_row_value(row, "has_pointcloud") or 0),
        "acceptance_recorded_at": _row_value(row, "acceptance_recorded_at"),
        "source_type": source,
        "provenance": make_provenance(source, provenance_status),
    }


def event_to_dict(row: sqlite3.Row) -> dict:
    mode = row["task_mode"] if "task_mode" in row.keys() else None
    source = mode if mode in ("simulation", "replay") else "simulation"
    return {
        "id": row["id"],
        "scene_id": row["scene_id"],
        "task_id": row["task_id"],
        "type": row["type"],
        "severity": row["severity"],
        "status": row["status"],
        "position": {"x": row["x"], "y": row["y"], "z": row["z"]},
        "description": row["description"],
        "confidence": row["confidence"],
        "image_ref": row["image_ref"],
        "detected_at": row["detected_at"],
        "handled_by": row["handled_by"],
        "handled_at": row["handled_at"],
        "handle_comment": row["handle_comment"],
        "source_type": source,
        "provenance": make_provenance(source, "simulated"),
    }


def device_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "scene_id": row["scene_id"],
        "name": row["name"],
        "type": row["type"],
        "unit": row["unit"],
        "position": {"x": row["x"], "y": row["y"], "z": row["z"]},
        "status": row["status"],
        "source_type": "simulation",
        # 设备类型/单位以甲方设备清单为准
        "provenance": make_provenance("simulation", "pending_confirmation"),
    }


def point_to_dict(row: sqlite3.Row, source: str, provenance_status: str = "simulated") -> dict:
    return {
        "task_id": row["task_id"],
        "seq": row["seq"],
        "timestamp": row["timestamp"],
        "position": {"x": row["x"], "y": row["y"], "z": row["z"]},
        "heading_deg": row["heading_deg"],
        "speed_mps": row["speed_mps"],
        "source_type": source,
        "provenance": make_provenance(source, provenance_status),
    }
