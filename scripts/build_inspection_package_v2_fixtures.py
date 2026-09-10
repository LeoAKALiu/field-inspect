#!/usr/bin/env python3
"""Build versioned Formal Inspection Package v2 golden BagIt fixtures."""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

import bagit

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services/api"))

from app.inspection_package import canonical_package_sha256, percentile_linear  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "data/contract-fixtures"
RUN_START = "2026-08-24T02:00:00Z"
RUN_END = "2026-08-24T02:00:10Z"
SCENE_ID = "scene-001"
SCENE_VERSION_ID = "scene-001-v1"
ALIGNMENT_ID = "alignment-scene-001-v1"
COORDINATE_SYSTEM = "scene_local_yup"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _minimal_png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = b"\x00\x00\x00\x00\xff"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(
        b"IEND", b""
    )


def _tiny_ply() -> str:
    return (
        "ply\n"
        "format ascii 1.0\n"
        "comment optional display artifact; Digital Twin does not promise rendering\n"
        "element vertex 1\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "end_header\n"
        "-52.0 1.0 -18.0\n"
    )


def _pose(x: float, y: float, z: float, yaw: float = 12.0) -> dict:
    return {
        "position": {"x": x, "y": y, "z": z},
        "rotation_rpy_deg": {"roll": 0.0, "pitch": 0.0, "yaw": yaw},
    }


def _corners(offset: float = 0.0) -> list[dict]:
    return [
        {"x": 120.0 + offset, "y": 80.0},
        {"x": 220.0 + offset, "y": 80.0},
        {"x": 220.0 + offset, "y": 180.0},
        {"x": 120.0 + offset, "y": 180.0},
    ]


def _observations(base_time: str, x: float, z: float, lineage: dict) -> list[dict]:
    translations = [0.008, 0.010, 0.012]
    rotations = [0.40, 0.50, 0.60]
    observations = []
    for seq, (translation, rotation) in enumerate(zip(translations, rotations, strict=True)):
        stamp = base_time.replace("Z", f".{100 * (seq + 1):03d}Z")
        observations.append(
            {
                "seq": seq,
                "mcap_frame_timestamp": stamp,
                "corners_px": _corners(seq * 2),
                "pose_camera": _pose(0.4, 0.0, 1.8 + seq * 0.01, 1.0),
                "pose_scene": _pose(x, 1.05, z, 12.0),
                "translation_residual_m": translation,
                "rotation_residual_deg": rotation,
                "camera_calibration_sha256": lineage["camera_calibration_sha256"],
                "detector": lineage["detector"],
                "transform_id": lineage["transform_id"],
                "transform_sha256": lineage["transform_sha256"],
            }
        )
    return observations


def _landmark(
    *,
    landmark_id: str,
    marker_id: int,
    role: str,
    route_portion: str | None,
    image_path: str,
    x: float,
    z: float,
    base_time: str,
    calibration_sha256: str,
    transform_sha256: str,
) -> dict:
    lineage = {
        "camera_calibration_sha256": calibration_sha256,
        "detector": {
            "name": "opencv_aruco",
            "version": "4.10.0",
            "configuration": {
                "dictionary": "4X4_50",
                "adaptiveThreshWinSizeMin": 3,
                "adaptiveThreshWinSizeMax": 23,
            },
        },
        "transform_id": "camera-to-scene-scene-001-v1",
        "transform_sha256": transform_sha256,
    }
    observations = _observations(base_time, x, z, lineage)
    landmark = {
        "landmark_id": landmark_id,
        "marker_id": marker_id,
        "dictionary": "4X4_50",
        "role": role,
        "physical_size_m": 0.2,
        "registered_pose": _pose(x, 1.05, z, 12.0),
        "pose_tolerance": {"translation_m": 0.05, "rotation_deg": 2.0},
        "min_valid_samples": 3,
        "annotated_image_path": image_path,
        "lineage": lineage,
        "observations": observations,
        "sample_count": len(observations),
        "translation_p95_m": percentile_linear(
            [item["translation_residual_m"] for item in observations]
        ),
        "rotation_p95_deg": percentile_linear(
            [item["rotation_residual_deg"] for item in observations]
        ),
    }
    if route_portion is not None:
        landmark["route_portion"] = route_portion
    return landmark


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


def _inventory_for(payload_root: Path) -> list[dict]:
    entries = []
    for path in sorted(payload_root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        relative = path.relative_to(payload_root).as_posix()
        entries.append({"path": relative, "sha256": _sha256_bytes(path.read_bytes())})
    return entries


def build_package(*, run_id: str, status: str, include_pointcloud: bool, include_auxiliary: bool) -> None:
    target = FIXTURE_ROOT / (
        "inspection-package-v2-completed"
        if status == "completed"
        else "inspection-package-v2-completed-with-exceptions"
    )
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    system_yaml = (
        "export:\n"
        "  package_contract: '2.0'\n"
        "  customer_instrument_data: false\n"
        "  parse_mcap_on_server: false\n"
    )
    calibration = _dump(
        {
            "model": "pinhole",
            "fx": 600.0,
            "fy": 600.0,
            "cx": 320.0,
            "cy": 240.0,
            "distortion": [0, 0, 0, 0, 0],
        }
    )
    evidence = _dump(
        {
            "alignment_id": ALIGNMENT_ID,
            "scene_version_id": SCENE_VERSION_ID,
            "method": "surveyed_rigid_transform",
            "coordinate_system": COORDINATE_SYSTEM,
        }
    )
    transform = _dump(
        {
            "transform_id": "camera-to-scene-scene-001-v1",
            "from": "camera_optical",
            "to": COORDINATE_SYSTEM,
            "translation_m": {"x": 0.12, "y": 1.05, "z": 0.04},
            "rotation_rpy_deg": {"roll": 0.0, "pitch": -2.0, "yaw": 12.0},
        }
    )
    scene_version = _dump(
        {
            "scene_id": SCENE_ID,
            "scene_version_id": SCENE_VERSION_ID,
            "coordinate_system": COORDINATE_SYSTEM,
        }
    )
    trajectory = {
        "schema_version": "2.0",
        "run_id": run_id,
        "scene_version_id": SCENE_VERSION_ID,
        "alignment_id": ALIGNMENT_ID,
        "coordinate_system": COORDINATE_SYSTEM,
        "points": [
            {
                "seq": 0,
                "timestamp": "2026-08-24T02:00:01Z",
                "position": {"x": -52.0, "y": 1.0, "z": -18.0},
                "heading_deg": 12.0,
                "speed_mps": 0.0,
                "battery_pct": 93.0,
            },
            {
                "seq": 1,
                "timestamp": "2026-08-24T02:00:04Z",
                "position": {"x": -51.8, "y": 1.0, "z": -17.96},
                "heading_deg": 12.0,
                "speed_mps": 0.2,
                "battery_pct": 93.0,
            },
            {
                "seq": 2,
                "timestamp": "2026-08-24T02:00:07Z",
                "position": {"x": -51.4, "y": 1.0, "z": -17.88},
                "heading_deg": 12.0,
                "speed_mps": 0.2,
                "battery_pct": 92.0,
            },
        ],
    }

    png = _minimal_png()
    _write(target / "config/system.yaml", system_yaml)
    _write(target / "config/camera_calibration.json", calibration)
    _write(target / "alignment/evidence.json", evidence)
    _write(target / "alignment/camera_to_scene.json", transform)
    _write(target / "scene/scene-version.json", scene_version)
    _write(target / "replay/trajectory.json", _dump(trajectory))
    _write(target / "bags/run.mcap", b"\x89MCAP0\r\nNOT_PARSED_BY_DIGITAL_TWIN\n")
    _write(target / "acceptance/first_frame.png", png)
    _write(target / "acceptance/last_frame.png", png)
    _write(target / "acceptance/landmarks/aruco-begin-01.png", png)
    _write(target / "acceptance/landmarks/aruco-mid-02.png", png)
    _write(target / "acceptance/landmarks/aruco-end-03.png", png)
    if include_auxiliary:
        _write(target / "acceptance/landmarks/aruco-aux-10.png", png)
    if include_pointcloud:
        _write(target / "artifacts/preview-pointcloud.ply", _tiny_ply())

    calibration_sha256 = _sha256_text(calibration)
    transform_sha256 = _sha256_text(transform)
    evidence_sha256 = _sha256_text(evidence)
    asset_sha256 = _sha256_text(scene_version)
    config_sha256 = _sha256_text(system_yaml)

    landmarks = [
        _landmark(
            landmark_id="aruco-begin-01",
            marker_id=1,
            role="required",
            route_portion="beginning",
            image_path="acceptance/landmarks/aruco-begin-01.png",
            x=-52.0,
            z=-18.0,
            base_time="2026-08-24T02:00:01Z",
            calibration_sha256=calibration_sha256,
            transform_sha256=transform_sha256,
        ),
        _landmark(
            landmark_id="aruco-mid-02",
            marker_id=2,
            role="required",
            route_portion="middle",
            image_path="acceptance/landmarks/aruco-mid-02.png",
            x=-51.8,
            z=-17.96,
            base_time="2026-08-24T02:00:04Z",
            calibration_sha256=calibration_sha256,
            transform_sha256=transform_sha256,
        ),
        _landmark(
            landmark_id="aruco-end-03",
            marker_id=3,
            role="required",
            route_portion="end",
            image_path="acceptance/landmarks/aruco-end-03.png",
            x=-51.4,
            z=-17.88,
            base_time="2026-08-24T02:00:07Z",
            calibration_sha256=calibration_sha256,
            transform_sha256=transform_sha256,
        ),
    ]
    if include_auxiliary:
        landmarks.append(
            _landmark(
                landmark_id="aruco-aux-10",
                marker_id=10,
                role="auxiliary",
                route_portion=None,
                image_path="acceptance/landmarks/aruco-aux-10.png",
                x=-51.6,
                z=-17.92,
                base_time="2026-08-24T02:00:05Z",
                calibration_sha256=calibration_sha256,
                transform_sha256=transform_sha256,
            )
        )

    exceptions = []
    artifacts = []
    if include_pointcloud:
        artifacts.append(
            {
                "path": "artifacts/preview-pointcloud.ply",
                "kind": "pointcloud",
                "display": True,
            }
        )
    else:
        exceptions.append(
            {
                "code": "display_artifact_not_provided",
                "message": "optional point cloud was not generated for this run",
            }
        )

    inventory = _inventory_for(target)
    manifest = {
        "schema_version": "2.0",
        "source_kind": "inspection_run",
        "run_id": run_id,
        "package_sha256": canonical_package_sha256(
            [(entry["path"], entry["sha256"]) for entry in inventory]
        ),
        "name": "Formal Inspection Package v2 golden fixture",
        "status": status,
        "exceptions": exceptions,
        "started_at": RUN_START,
        "ended_at": RUN_END,
        "coordinate_system": COORDINATE_SYSTEM,
        "scene_version": {
            "scene_id": SCENE_ID,
            "scene_version_id": SCENE_VERSION_ID,
            "asset_sha256": asset_sha256,
        },
        "alignment": {
            "alignment_id": ALIGNMENT_ID,
            "evidence_sha256": evidence_sha256,
        },
        "git_commit": "deadbeef",
        "config_sha256": config_sha256,
        "model_version": None,
        "bag": {"storage_id": "mcap", "path": "bags/run.mcap"},
        "replay": {"trajectory_path": "replay/trajectory.json"},
        "acceptance_evidence": {
            "first_frame": {
                "path": "acceptance/first_frame.png",
                "timestamp": "2026-08-24T02:00:01Z",
                "mcap_frame_timestamp": "2026-08-24T02:00:01Z",
            },
            "last_frame": {
                "path": "acceptance/last_frame.png",
                "timestamp": "2026-08-24T02:00:07Z",
                "mcap_frame_timestamp": "2026-08-24T02:00:07Z",
            },
            "landmarks": landmarks,
        },
        "artifacts": artifacts,
        "payload_inventory": inventory,
        "exit_reason": (
            "golden_fixture_complete"
            if status == "completed"
            else "golden_fixture_completed_with_exceptions"
        ),
    }
    _write(target / "manifest.json", _dump(manifest))

    bagit.make_bag(
        str(target),
        bag_info={
            "Bag-Software-Agent": "digital-twin-inspection-package 2.0",
            "Bagging-Date": "2026-08-24",
            "External-Identifier": run_id,
            "Internal-Sender-Description": (
                "Golden Formal Inspection Package v2 fixture; not a field recording"
            ),
            "Source-Organization": "Digital Twin contract lock",
        },
        checksums=["sha256"],
    )
    (target / "bagit.txt").write_text(
        "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n",
        encoding="utf-8",
    )
    bagit.Bag(str(target)).save(manifests=False)


def main() -> None:
    build_package(
        run_id="golden-run-v2-completed",
        status="completed",
        include_pointcloud=True,
        include_auxiliary=True,
    )
    build_package(
        run_id="golden-run-v2-completed-with-exceptions",
        status="completed_with_exceptions",
        include_pointcloud=False,
        include_auxiliary=False,
    )
    print("wrote v2 golden packages under data/contract-fixtures/")


if __name__ == "__main__":
    main()
