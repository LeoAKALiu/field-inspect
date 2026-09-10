"""Derive conservative 2D marker observations from immutable station captures."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path, PurePosixPath

from .domain_contract import InstrumentObservation, InstrumentLocalization
from .instruments import encoded, instant, record, save
from .writer_lock import ImportWriterLock

VERSION = "marker-proxy-2d-v1"


def process(db, attempt_id, capture_sequence):
    evidence = record(db, "station_evidence", attempt_id)
    run_id = evidence["run_id"]
    ledger = db.query_one("SELECT * FROM run_bundle_imports WHERE run_id=?", (run_id,))
    if ledger is None:
        raise ValueError("run archive is missing")
    archive = Path(ledger["archive_path"])
    with ImportWriterLock(archive.parent.parent):
        return _process(db, evidence, ledger, archive, capture_sequence)


def _process(db, evidence, ledger, archive, sequence):
    from . import run_bundle as bundles
    import bagit
    bundles._assert_plain_tree(archive)
    bag = bagit.Bag(str(archive)); bag.validate()
    manifest, _ = bundles._validate_semantics(archive, bag)
    if manifest.run_id != evidence["run_id"] or bundles._bundle_hash(bag) != ledger["bundle_sha256"]:
        raise ValueError("archive no longer matches import ledger")
    inventory = {entry.path: entry.sha256 for entry in getattr(manifest, "payload_inventory", [])}
    selected = [c for c in evidence["captures"] if c["capture_attempt_seq"] == sequence and c["state"] == "captured"]
    if len(selected) != 1:
        raise ValueError("exactly one successful capture is required")
    capture = selected[0]
    folder = str(PurePosixPath(capture["artifact_path"]).parent)
    if inventory.get(capture["artifact_path"]) != capture["artifact_sha256"]:
        raise ValueError("indexed capture changed")
    inputs = {"package": ledger["bundle_sha256"], "attempt": evidence["attempt_id"], "capture": sequence, "version": VERSION}
    report_id = "processing-" + hashlib.sha256(encoded(inputs).encode()).hexdigest()[:24]
    existing = db.query_one("SELECT payload FROM instrument_records WHERE kind='processing_report' AND id=?", (report_id,))
    if existing:
        return json.loads(existing["payload"])

    def read(path, limit=4*1024*1024):
        parts = PurePosixPath(path)
        if path not in inventory or parts.is_absolute() or ".." in parts.parts or "\\" in path or ":" in path:
            raise ValueError("input is not an indexed relative payload")
        file = archive / "data" / path
        if file.stat().st_size > limit:
            raise ValueError("processing input exceeds size limit")
        raw = file.read_bytes()
        if hashlib.sha256(raw).hexdigest() != inventory[path]:
            raise ValueError("processing input digest mismatch")
        return raw

    required = [folder + "/frame.png", folder + "/source.json", "config/camera_calibration.json", "config/instrument-detector.json"]
    gaps = ["missing:" + path for path in required if path not in inventory]
    report = {"report_id": report_id, "run_id": manifest.run_id, "attempt_id": evidence["attempt_id"],
        "capture_attempt_seq": sequence, "producer_version": VERSION, "input_sha256": inputs,
        "status": "blocked", "gaps": gaps, "observations": [], "detections": [],
        "notice": "Marker proxy only. Confidence is conservatively zero; no calibrated detection probability or 3D confirmation is claimed."}
    objects = []
    if not gaps:
        config = json.loads(read("config/instrument-detector.json"))
        camera = json.loads(read("config/camera_calibration.json"))
        source = json.loads(read(folder + "/source.json"))
        if not all(isinstance(item, dict) for item in (config, camera, source)):
            raise ValueError("processing configuration must contain JSON objects")
        if camera.get("verified") is not True:
            gaps.append("camera_calibration_not_verified")
        if not source.get("pose"):
            gaps.append("localization:complete_capture_pose_missing")
        gaps.append("localization:metric_pose_solver_and_uncertainty_not_available")
        if camera.get("verified") is True:
            import cv2
            import numpy as np
            dictionary = config.get("dictionary", "")
            ids = config.get("marker_ids")
            if config.get("schema_version") != "1.0" or not isinstance(dictionary, str) or not dictionary.startswith("DICT_") or not hasattr(cv2.aruco, dictionary):
                raise ValueError("explicit supported detector dictionary is required")
            if not isinstance(ids, list) or not ids or len(ids) > 512 or any(type(i) is not int or i < 0 for i in ids) or len(set(ids)) != len(ids):
                raise ValueError("explicit unique instrument proxy marker_ids required")
            landmarks = db.query_all("SELECT dictionary,marker_id FROM registered_landmarks WHERE alignment_id=?", (manifest.alignment.alignment_id,))
            if any(row["dictionary"] == dictionary and row["marker_id"] in ids for row in landmarks):
                raise ValueError("acceptance landmarks cannot be instrument proxies")
            if not source.get("frame_id") or camera.get("frame_id") != source["frame_id"]:
                raise ValueError("camera calibration frame must match captured image frame")
            matrix = np.asarray(camera.get("camera_matrix"), dtype=float)
            distortion = np.asarray(camera.get("distortion_coefficients"), dtype=float)
            if matrix.size != 9 or not np.isfinite(matrix).all() or distortion.size not in {4,5,8,12,14} or not np.isfinite(distortion).all():
                raise ValueError("invalid camera calibration arrays")
            matrix = matrix.reshape(3,3)
            if matrix[0,0] <= 0 or matrix[1,1] <= 0:
                raise ValueError("camera focal lengths must be positive")
            raw_image = read(folder + "/frame.png", 32*1024*1024)
            # Inspect PNG dimensions before OpenCV allocates the decoded frame.
            if raw_image[:8] != b"\x89PNG\r\n\x1a\n" or len(raw_image) < 24:
                raise ValueError("capture must be a PNG")
            width = int.from_bytes(raw_image[16:20], "big"); height = int.from_bytes(raw_image[20:24], "big")
            if not 0 < width*height <= 20_000_000 or camera.get("image_width") != width or camera.get("image_height") != height:
                raise ValueError("capture dimensions must match calibration and size limit")
            frame = cv2.imdecode(np.frombuffer(raw_image, np.uint8), cv2.IMREAD_GRAYSCALE)
            if frame is None or frame.shape != (height, width):
                raise ValueError("capture image could not be decoded")
            dictionary_value = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary))
            if any(i >= len(dictionary_value.bytesList) for i in ids):
                raise ValueError("configured marker ID is outside dictionary")
            detector = cv2.aruco.ArucoDetector(dictionary_value, cv2.aruco.DetectorParameters())
            corners, detected_ids, _ = detector.detectMarkers(frame)
            stamp = source.get("image_header_stamp_ns")
            if type(stamp) is not int or stamp <= 0:
                raise ValueError("capture image timestamp is missing")
            seconds, nanos = divmod(stamp, 1000000000)
            captured = datetime.fromtimestamp(seconds, timezone.utc) + timedelta(microseconds=nanos//1000)
            if not instant(str(manifest.started_at)) <= captured <= instant(str(manifest.ended_at)):
                raise ValueError("capture timestamp is outside run")
            source_type = "replay" if manifest.source_kind == "inspection_run" else "simulation"
            common = {"contract_version": "0.3", "run_id": manifest.run_id, "source_type": source_type,
                "provenance": {"source": source_type, "status": "pending_confirmation" if source_type == "replay" else "simulated"},
                "lineage": {"source": source_type, "producer": VERSION, "producer_version": cv2.__version__, "derived_from": report_id}}
            detections = [] if detected_ids is None else list(zip(detected_ids.flatten().tolist(), corners))
            # Stable ordering retains multiple occurrences of the same marker for review.
            detections.sort(key=lambda item: (item[0], item[1].flatten().tolist()))
            for index, (marker_id, points) in enumerate(detections):
                if marker_id not in ids:
                    continue
                points = points.reshape(4,2)
                lo = np.maximum(points.min(axis=0), [0,0]); hi = np.minimum(points.max(axis=0), [width,height])
                observation_id = report_id + f"-obs-{index}"
                observation = InstrumentObservation.model_validate({**common, "observation_id": observation_id,
                    "captured_at": captured.isoformat(), "image_region": {"x_min": float(lo[0]/width), "y_min": float(lo[1]/height),
                        "x_max": float(hi[0]/width), "y_max": float(hi[1]/height), "image_width": width, "image_height": height},
                    "detector": {"kind": "marker_proxy", "version": VERSION + "/opencv-" + cv2.__version__},
                    "confidence": 0, "calibration_ref": "sha256:" + inventory["config/camera_calibration.json"]}).model_dump(mode="json")
                local = InstrumentLocalization.model_validate({**common, "localization_id": observation_id + "-local",
                    "observation_id": observation_id, "status": "unmatched", "position": None, "heading_deg": None,
                    "method": "2d_only_metric_localization_unavailable", "residual_m": None}).model_dump(mode="json")
                objects.extend([("observation", observation_id, observation), ("localization", local["localization_id"], local)])
                report["observations"].append(observation_id)
                report["detections"].append({"observation_id": observation_id, "dictionary": dictionary, "marker_id": marker_id,
                    "corners_px": points.tolist()})
            report["status"] = "observed_2d" if objects else "no_configured_marker_detected"
            report["opencv_version"] = cv2.__version__
            report["input_sha256"].update({path: inventory[path] for path in required})
    with db.transaction() as conn:
        for kind, identity, value in objects:
            save(db, kind, identity, manifest.run_id, value, "station_processor", conn=conn)
        save(db, "processing_report", report_id, manifest.run_id, report, "station_processor", conn=conn)
    return report
