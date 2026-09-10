"""场景查询与场景元数据（SceneMetadata）。"""

import json

from fastapi import APIRouter, Depends, Query

from ..db import Database, device_to_dict, scene_to_dict
from ..errors import not_found
from ..provenance import make_provenance
from . import get_db

router = APIRouter()


def _scene_version_payload(db: Database, scene_version_id: str) -> dict | None:
    row = db.query_one(
        "SELECT scene_version_id, scene_id, asset_sha256, navigation_default "
        "FROM scene_versions WHERE scene_version_id = ?",
        (scene_version_id,),
    )
    if row is None:
        return None
    alignment = db.query_one(
        "SELECT alignment_id, evidence_sha256 FROM verified_alignments "
        "WHERE scene_version_id = ? ORDER BY registered_at ASC",
        (scene_version_id,),
    )
    return {
        "scene_version_id": row["scene_version_id"],
        "scene_id": row["scene_id"],
        "asset_sha256": row["asset_sha256"],
        "navigation_default": bool(row["navigation_default"]),
        "alignment_id": alignment["alignment_id"] if alignment else None,
        "evidence_sha256": alignment["evidence_sha256"] if alignment else None,
    }


def _attach_scene_version(meta: dict, version: dict | None) -> dict:
    if version is None:
        return meta
    meta["scene_version_id"] = version["scene_version_id"]
    meta["asset_sha256"] = version["asset_sha256"]
    meta["alignment_id"] = version["alignment_id"]
    return meta


@router.get("/scenes")
def list_scenes(db: Database = Depends(get_db)) -> list[dict]:
    rows = db.query_all("SELECT * FROM scenes ORDER BY created_at ASC, id ASC")
    return [scene_to_dict(r) for r in rows]


# 注意：/scenes/{scene_id}/metadata 必须先于 /scenes/{scene_id} 注册
@router.get("/scene-versions/{scene_version_id}")
def get_scene_version(scene_version_id: str, db: Database = Depends(get_db)) -> dict:
    payload = _scene_version_payload(db, scene_version_id)
    if payload is None:
        not_found("场景版本", scene_version_id)
    return payload


@router.get("/scenes/{scene_id}/metadata")
def get_scene_metadata(
    scene_id: str,
    scene_version_id: str | None = Query(default=None),
    db: Database = Depends(get_db),
) -> dict:
    row = db.query_one("SELECT data FROM scene_metadata WHERE scene_id = ?", (scene_id,))
    if row is None:
        if db.query_one("SELECT id FROM scenes WHERE id = ?", (scene_id,)) is None:
            not_found("场景", scene_id)
        not_found("场景元数据", scene_id)
    meta = json.loads(row["data"])
    if scene_version_id:
        version = _scene_version_payload(db, scene_version_id)
        if version is None or version["scene_id"] != scene_id:
            not_found("场景版本", scene_version_id)
        return _attach_scene_version(meta, version)
    default = db.query_one(
        "SELECT scene_version_id FROM scene_versions "
        "WHERE scene_id = ? AND navigation_default = 1 "
        "ORDER BY registered_at DESC",
        (scene_id,),
    )
    if default is None:
        return meta
    return _attach_scene_version(meta, _scene_version_payload(db, default["scene_version_id"]))


@router.get("/scenes/{scene_id}")
def get_scene(scene_id: str, db: Database = Depends(get_db)) -> dict:
    row = db.query_one("SELECT * FROM scenes WHERE id = ?", (scene_id,))
    if row is None:
        not_found("场景", scene_id)
    scene = scene_to_dict(row)
    scene["spatial_bindings"] = _bindings_for_scene(db, scene_id)
    return scene


def _bindings_for_scene(db: Database, scene_id: str) -> list[dict]:
    """空间绑定由设备与事件位置动态派生。坐标系固定 scene_local_yup。"""
    bindings: list[dict] = []
    for d in db.query_all("SELECT * FROM devices WHERE scene_id = ? ORDER BY id", (scene_id,)):
        dev = device_to_dict(d)
        bindings.append(
            {
                "id": f"bind-dev-{dev['id']}",
                "scene_id": scene_id,
                "target_type": "sensor_device",
                "target_id": dev["id"],
                "position": dev["position"],
                "coordinate_system": "scene_local_yup",
                "source_type": "simulation",
                "provenance": make_provenance("simulation", "pending_confirmation"),
            }
        )
    for e in db.query_all("SELECT * FROM events WHERE scene_id = ? ORDER BY id", (scene_id,)):
        bindings.append(
            {
                "id": f"bind-evt-{e['id']}",
                "scene_id": scene_id,
                "target_type": "detection_event",
                "target_id": e["id"],
                "position": {"x": e["x"], "y": e["y"], "z": e["z"]},
                "coordinate_system": "scene_local_yup",
                "source_type": "simulation",
                "provenance": make_provenance("simulation", "pending_confirmation"),
            }
        )
    return bindings
