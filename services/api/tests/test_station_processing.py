"""Synthetic images go through v2 import and the actual local processing API."""
import hashlib
import json
import uuid
from datetime import datetime

import cv2
import numpy as np
import pytest

from tests.formal_helpers import (
    COMPLETED,
    import_named,
    inbox_copy,
    refresh_inventory,
    register_golden_scene,
)


@pytest.fixture()
def processing_package(client):
    register_golden_scene(client)
    package = inbox_copy(client, COMPLETED, "synthetic-station-processing")
    data = package / "data"
    manifest = json.loads((data / "manifest.json").read_text())
    run = manifest["run_id"]
    key = (run, "fixture-execution", "fixture-station")
    attempt = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(list(key))).hex
    identity = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]
    folder = f"artifacts/station-captures/{identity}/1"
    capture = data / folder
    capture.mkdir(parents=True)
    frame = np.full((400, 400), 255, dtype=np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    frame[120:280, 120:280] = cv2.aruco.generateImageMarker(dictionary, 42, 160)
    assert cv2.imwrite(str(capture / "frame.png"), frame)
    started = datetime.fromisoformat(manifest["started_at"].replace("Z", "+00:00"))
    stamp = int(started.timestamp() * 1e9)
    end = int(datetime.fromisoformat(manifest["ended_at"].replace("Z", "+00:00")).timestamp() * 1e9)
    for state in ("started", "captured"):
        (capture / f"{state}.json").write_text(json.dumps({
            "run_id": run, "execution_id": key[1], "station_id": key[2],
            "attempt_seq": 1, "state": state, "reason": "synthetic_fixture",
            "recorded_at_ns": stamp,
            "image_path": "frame.png" if state == "captured" else None,
            "image_sha256": hashlib.sha256((capture / "frame.png").read_bytes()).hexdigest()
            if state == "captured" else None,
        }))
    (capture / "source.json").write_text(json.dumps({
        "schema_version": "1.2", "frame_id": "fixture-camera", "image_header_stamp_ns": stamp,
    }))
    (data / "config/camera_calibration.json").write_text(json.dumps({
        "verified": True, "frame_id": "fixture-camera", "width": 400, "height": 400,
        "camera_matrix": [[500, 0, 200], [0, 500, 200], [0, 0, 1]],
        "distortion_coefficients": [0, 0, 0, 0, 0],
    }))
    (data / "config/instrument-detector.json").write_text(json.dumps({
        "schema_version": "1.0", "dictionary": "DICT_4X4_50", "marker_ids": [42],
    }))
    index = data / "artifacts/stations/attempts.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps({
        "schema_version": "1.0", "artifact_kind": "vehicle_station_attempts", "run_id": run,
        "run_status": manifest["status"], "producer": "synthetic_fixture",
        "producer_version": "fixture", "generated_at": manifest["ended_at"],
        "source_files": [{"path": "config/system.yaml", "sha256":
            hashlib.sha256((data / "config/system.yaml").read_bytes()).hexdigest()}],
        "attempts": [{
            "attempt_id": attempt, "run_id": run, "execution_id": key[1], "station_id": key[2],
            "attempt_seq": 1, "result": "success", "reason_code": None,
            "mcap_range": {"bag_path": f"bag/{run}", "start_ns": stamp, "end_ns": end,
                           "clock": "rosbag_receive_time",
                           "bounds_kind": "execution_to_result_status"},
        }],
    }))
    operational = data / "metadata/jetson-manifest.json"
    operational.parent.mkdir(parents=True, exist_ok=True)
    operational.write_text(json.dumps({"station_attempts": {
        "path": "artifacts/stations/attempts.json",
        "sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
        "generated_at": manifest["ended_at"], "producer_version": "fixture",
    }}))
    return package, run, attempt, folder


@pytest.mark.parametrize("mode", [
    "detected", "legacy_dimensions", "conflicting_dimensions", "no_detection",
    "unverified", "missing_detector",
])
def test_processing_persists_traceable_results_and_input_gaps(client, processing_package, mode):
    package, run, attempt, folder = processing_package
    data = package / "data"
    if mode == "no_detection":
        detector = data / "config/instrument-detector.json"
        config = json.loads(detector.read_text())
        config["marker_ids"] = [41]
        detector.write_text(json.dumps(config))
    elif mode == "unverified":
        camera = data / "config/camera_calibration.json"
        config = json.loads(camera.read_text())
        config["verified"] = False
        camera.write_text(json.dumps(config))
    elif mode == "missing_detector":
        (data / "config/instrument-detector.json").unlink()
    elif mode in {"legacy_dimensions", "conflicting_dimensions"}:
        camera = data / "config/camera_calibration.json"
        config = json.loads(camera.read_text())
        if mode == "legacy_dimensions":
            config["image_width"] = config.pop("width")
            config["image_height"] = config.pop("height")
        else:
            config["image_width"] = 401
        camera.write_text(json.dumps(config))
    # Keep the synthetic acceptance evidence bound to the synthetic camera bytes.
    manifest_file = data / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    camera_sha = hashlib.sha256((data / "config/camera_calibration.json").read_bytes()).hexdigest()
    for landmark in manifest["acceptance_evidence"]["landmarks"]:
        landmark["lineage"]["camera_calibration_sha256"] = camera_sha
        for observation in landmark["observations"]:
            observation["camera_calibration_sha256"] = camera_sha
    manifest_file.write_text(json.dumps(manifest))
    refresh_inventory(package)
    original = {p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    assert import_named(client, package.name)["status"] == "imported"
    url = f"/api/instruments/stations/{attempt}/captures/1/process"
    response = client.post(url)
    if mode == "conflicting_dimensions":
        assert response.status_code == 422
        assert "conflicting aliases" in response.json()["error"]["message"]
        assert not client.get(f"/api/instruments/records/processing_report?run_id={run}").json()
        remaining = {
            p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()
        }
        assert remaining == original
        return
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == {
        "detected": "observed_2d", "legacy_dimensions": "observed_2d",
        "no_detection": "no_configured_marker_detected",
        "unverified": "blocked", "missing_detector": "blocked",
    }[mode]
    assert report["run_id"] == run and report["attempt_id"] == attempt
    assert report["capture_attempt_seq"] == 1
    for path in (folder + "/frame.png", folder + "/source.json", "config/camera_calibration.json"):
        digest = hashlib.sha256((data / path).read_bytes()).hexdigest()
        assert report["input_sha256"][path] == digest
    if mode in {"detected", "legacy_dimensions"}:
        assert len(report["observations"]) == 1
        assert report["detections"][0]["marker_id"] == 42
        assert report["localization_estimates"][0].get("position") is None
        item = client.get(f"/api/instruments/runs/{run}/marker-localizations").json()["items"][0]
        assert item["localization"]["position"] is None
        assert item["localization"]["status"] == "unmatched"
        assert item["report_id"] == report["report_id"]
        assert item["gaps"]
    else:
        assert not report["observations"]
    assert client.post(url).json() == report
    stored = client.get(f"/api/instruments/records/processing_report/{report['report_id']}")
    assert stored.json() == report
    assert client.get(f"/api/tasks/{run}").json()["acceptance_state"] == "pending_acceptance"
    remaining = {p.relative_to(package): p.read_bytes() for p in package.rglob("*") if p.is_file()}
    assert remaining == original
