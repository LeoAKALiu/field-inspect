"""Derive complete ArUco acceptance evidence from a finalized recorded MCAP.

No image subsampling or best-residual selection. The first valid observation is
the representative image; every valid detection remains in the residual series.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from inspection_pipeline.formal_package import _contract, _json, _write


def _matrix(value):
    matrix = np.asarray(value, dtype=float)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
            or not np.allclose(matrix[3], [0, 0, 0, 1])
            or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(matrix[:3, :3]), 1, atol=1e-6)):
        raise ValueError("expected a finite rigid 4x4 transform")
    return matrix


def _pose(matrix):
    return {"position": dict(zip(("x", "y", "z"), matrix[:3, 3].tolist())),
            "rotation_rpy_deg": dict(zip(("roll", "pitch", "yaw"),
                Rotation.from_matrix(matrix[:3, :3]).as_euler("xyz", degrees=True).tolist()))}


def _time(ns):
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat().replace("+00:00", "Z")


def _stamp(message):
    return message.header.stamp.sec * 1_000_000_000 + message.header.stamp.nanosec


def export_acceptance(run_dir: Path) -> Path:
    # Imported only on the ROS target, not while reading/building platform code.
    import cv2
    import rosbag2_py
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import Image
    from rclpy.serialization import deserialize_message
    from inspection_pipeline.bundle_export import _assert_plain_tree, _fsync_tree
    from inspection_pipeline.trajectory_export import load_scene_alignment

    _assert_plain_tree(run_dir)
    run = _json(run_dir / "manifest.json")
    if run.get("run_mode") != "production" or run.get("status") not in {
        "completed", "completed_with_exceptions", "incomplete", "aborted", "failed"
    }:
        raise ValueError("acceptance derivation requires a finalized production recording")
    target = run_dir / "acceptance"
    if target.exists():
        raise ValueError("acceptance output already exists; preserve it and use a new run copy")
    config = _json(run_dir / "config/acceptance-export.json")
    camera = _json(run_dir / "config/camera_calibration.json")
    transform = _json(run_dir / "alignment/camera_to_scene.json")
    scene = _json(run_dir / "scene/scene-version.json")
    alignment, _ = load_scene_alignment(run_dir / "config/scene_alignment.yaml")
    if camera.get("verified") is not True or transform.get("verified") is not True:
        raise ValueError("verified camera calibration and extrinsics are required")
    if transform.get("alignment_id") != alignment.alignment_id:
        raise ValueError("camera transform alignment identity mismatch")
    if transform.get("scene_alignment_sha256") != alignment.source_sha256:
        raise ValueError("camera transform must bind the exact measured scene alignment")
    base_from_camera = _matrix(transform["base_from_camera"])
    scene_from_map = np.eye(4)
    scene_from_map[:3, :3] = Rotation.from_quat(alignment.transform.rotation).as_matrix()
    scene_from_map[:3, 3] = alignment.transform.translation
    intrinsic = np.asarray(camera["camera_matrix"], dtype=float).reshape(3, 3)
    distortion = np.asarray(camera["distortion_coefficients"], dtype=float)
    if not np.isfinite(intrinsic).all() or not np.isfinite(distortion).all():
        raise ValueError("camera calibration contains non-finite values")
    if intrinsic[0, 0] <= 0 or intrinsic[1, 1] <= 0:
        raise ValueError("camera focal length must be positive")
    dictionary_name = config["dictionary"]
    if not dictionary_name.startswith("DICT_") or not hasattr(cv2.aruco, dictionary_name):
        raise ValueError("explicit supported ArUco dictionary required")
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_name))
    parameters = (cv2.aruco.DetectorParameters() if hasattr(cv2.aruco, "DetectorParameters")
                  else cv2.aruco.DetectorParameters_create())
    for key, value in config["detector_parameters"].items():
        if not hasattr(parameters, key):
            raise ValueError(f"unknown ArUco parameter: {key}")
        setattr(parameters, key, value)
    detector = {"name": "opencv_aruco", "version": cv2.__version__, "configuration": config}
    contract = _contract()
    lineage = {"camera_calibration_sha256": contract._file_sha256(run_dir / "config/camera_calibration.json"),
               "transform_id": transform["transform_id"],
               "transform_sha256": contract._file_sha256(run_dir / "alignment/camera_to_scene.json"),
               "detector": detector}
    landmarks = {}
    for item in scene["landmarks"]:
        if item["dictionary"] != dictionary_name or item["marker_id"] in landmarks:
            raise ValueError("landmarks require one explicit dictionary and unique marker IDs")
        landmarks[item["marker_id"]] = item
    if not landmarks:
        raise ValueError("no registered ArUco landmarks")
    max_gap = float(config["max_pose_gap_seconds"])
    if not np.isfinite(max_gap) or max_gap <= 0:
        raise ValueError("max_pose_gap_seconds must be finite and positive")

    def reader(topic):
        result = rosbag2_py.SequentialReader()
        result.open(rosbag2_py.StorageOptions(uri=str(run_dir / "bag" / run["run_id"]), storage_id="mcap"),
                    rosbag2_py.ConverterOptions("", ""))
        result.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
        return result

    poses, times = [], []
    stream = reader(config["pose_topic"])
    while stream.has_next():
        _, serialized, _ = stream.read_next()
        msg = deserialize_message(serialized, Odometry)
        if msg.header.frame_id != alignment.input_frame or msg.child_frame_id != transform["base_frame"]:
            raise ValueError("odometry frames disagree with registered camera transform")
        stamp = _stamp(msg)
        if times and stamp <= times[-1]:
            raise ValueError("pose timestamps must increase strictly")
        matrix = np.eye(4)
        q, p = msg.pose.pose.orientation, msg.pose.pose.position
        matrix[:3, :3] = Rotation.from_quat([q.x, q.y, q.z, q.w]).as_matrix()
        matrix[:3, 3] = [p.x, p.y, p.z]
        poses.append(_matrix(matrix)); times.append(stamp)
    if len(times) < 2:
        raise ValueError("insufficient recorded poses for camera placement")

    def camera_pose(stamp):
        index = bisect.bisect_right(times, stamp)
        if stamp == times[-1]:
            return scene_from_map @ poses[-1] @ base_from_camera
        if index == 0 or index == len(times):
            raise ValueError("camera timestamp outside recorded pose coverage")
        left, right = index - 1, index
        if (times[right] - times[left]) / 1e9 > max_gap:
            raise ValueError("recorded pose gap exceeds configured limit")
        fraction = (stamp - times[left]) / (times[right] - times[left])
        matrix = np.eye(4)
        matrix[:3, :3] = Slerp([0, 1], Rotation.from_matrix(
            np.stack([poses[left][:3, :3], poses[right][:3, :3]])))(fraction).as_matrix()
        matrix[:3, 3] = (1-fraction)*poses[left][:3, 3] + fraction*poses[right][:3, 3]
        return scene_from_map @ matrix @ base_from_camera

    temporary = Path(tempfile.mkdtemp(prefix=".acceptance-", dir=run_dir))
    try:
        (temporary / "landmarks").mkdir()
        result, counts = {}, {"frames": 0, "unregistered_detections": 0, "invalid_pose_detections": 0}
        first = last = None
        previous_receive = previous_header = -1
        stream = reader(config["image_topic"])
        while stream.has_next():
            _, serialized, receive_ns = stream.read_next()
            msg = deserialize_message(serialized, Image)
            stamp = _stamp(msg)
            if receive_ns <= previous_receive or stamp <= previous_header:
                raise ValueError("camera source and recording timestamps must increase strictly")
            previous_receive, previous_header = receive_ns, stamp
            if msg.header.frame_id != transform["camera_frame"]:
                raise ValueError("image optical frame differs from calibrated frame")
            if msg.width != camera["width"] or msg.height != camera["height"]:
                raise ValueError("recorded image size differs from calibration")
            channels = {"bgr8": 3, "rgb8": 3, "mono8": 1}.get(msg.encoding)
            if channels is None or msg.step < msg.width * channels:
                raise ValueError("unsupported image encoding or stride")
            pixels = np.frombuffer(bytes(msg.data), dtype=np.uint8).reshape(msg.height, msg.step)
            frame = pixels[:, :msg.width*channels].reshape(msg.height, msg.width, channels).copy()
            if channels == 1: frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif msg.encoding == "rgb8": frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            scene_from_camera = camera_pose(stamp)
            frame_ref = {"timestamp": _time(receive_ns), "mcap_frame_timestamp": _time(receive_ns)}
            if first is None:
                if not cv2.imwrite(str(temporary / "first_frame.png"), frame): raise OSError("image write failed")
                first = {"path": "acceptance/first_frame.png", **frame_ref}
            if not cv2.imwrite(str(temporary / "last_frame.png"), frame): raise OSError("image write failed")
            last = {"path": "acceptance/last_frame.png", **frame_ref}
            counts["frames"] += 1
            corners, ids, _ = cv2.aruco.detectMarkers(frame, dictionary, parameters=parameters)
            for detected_corners, marker_id in zip(corners, [] if ids is None else ids.flatten()):
                marker_id = int(marker_id)
                if marker_id not in landmarks:
                    counts["unregistered_detections"] += 1; continue
                landmark = landmarks[marker_id]
                half = float(landmark["physical_size_m"]) / 2
                if half <= 0 or not np.isfinite(half): raise ValueError("invalid marker size")
                object_points = np.array([[-half, half, 0], [half, half, 0], [half, -half, 0], [-half, -half, 0]])
                ok, rvec, tvec = cv2.solvePnP(object_points, detected_corners.reshape(4, 2), intrinsic,
                                            distortion, flags=cv2.SOLVEPNP_IPPE_SQUARE)
                if not ok or not np.isfinite(tvec).all() or not np.isfinite(rvec).all() or tvec[2, 0] <= 0:
                    counts["invalid_pose_detections"] += 1; continue
                camera_from_marker = np.eye(4)
                camera_from_marker[:3, :3] = cv2.Rodrigues(rvec)[0]
                camera_from_marker[:3, 3] = tvec.flatten()
                measured = scene_from_camera @ camera_from_marker
                registered = landmark["registered_pose"]
                expected_rotation = Rotation.from_euler("xyz", [registered["rotation_rpy_deg"][k]
                    for k in ("roll", "pitch", "yaw")], degrees=True)
                translation = float(np.linalg.norm(measured[:3, 3] - [registered["position"][k] for k in ("x", "y", "z")]))
                angle = float((expected_rotation.inv() * Rotation.from_matrix(measured[:3, :3])).magnitude()*180/np.pi)
                if marker_id not in result:
                    filename = f"marker-{marker_id}.png"
                    annotated = frame.copy(); cv2.aruco.drawDetectedMarkers(annotated, [detected_corners])
                    if not cv2.imwrite(str(temporary / "landmarks" / filename), annotated): raise OSError("image write failed")
                    keys = ("landmark_id", "marker_id", "dictionary", "role", "physical_size_m", "registered_pose", "pose_tolerance", "min_valid_samples", "route_portion")
                    result[marker_id] = {k: landmark[k] for k in keys if k in landmark}
                    result[marker_id].update(annotated_image_path=f"acceptance/landmarks/{filename}", lineage=lineage, observations=[])
                observations = result[marker_id]["observations"]
                observations.append({"seq": len(observations), "mcap_frame_timestamp": _time(receive_ns),
                    "corners_px": [{"x": float(x), "y": float(y)} for x,y in detected_corners.reshape(4,2)],
                    "pose_camera": _pose(camera_from_marker), "pose_scene": _pose(measured),
                    "translation_residual_m": translation, "rotation_residual_deg": angle, **lineage})
        if first is None or not result:
            raise ValueError("recording contains no image frames or no valid registered ArUco observations")
        for item in result.values():
            item.update(sample_count=len(item["observations"]),
                translation_p95_m=contract.percentile_linear([o["translation_residual_m"] for o in item["observations"]]),
                rotation_p95_deg=contract.percentile_linear([o["rotation_residual_deg"] for o in item["observations"]]))
        document = {"first_frame": first, "last_frame": last, "landmarks": list(result.values())}
        contract.AcceptanceEvidence.model_validate(document)
        _write(temporary / "evidence.json", document)
        _write(temporary / "derivation.json", {"run_id": run["run_id"], "counts": counts,
            "frame_clock": "rosbag_receive_time", "pose_clock": "header_stamp",
            "representative_policy": "first_valid_detection", "config_sha256": contract._file_sha256(run_dir / "config/acceptance-export.json"),
            "bag_inventory": [{"path": p.relative_to(run_dir).as_posix(), "sha256": contract._file_sha256(p)}
                for p in sorted((run_dir / "bag" / run["run_id"]).glob("*.mcap"))]})
        _fsync_tree(temporary)
        os.replace(temporary, target)
    except BaseException:
        import shutil
        if temporary.exists(): shutil.rmtree(temporary)
        raise
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    print(export_acceptance(parser.parse_args(argv).run_dir))
    return 0
