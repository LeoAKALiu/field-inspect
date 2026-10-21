"""Synthetic metadata at the metric-localization input boundary; no PnP calls."""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from app.marker_localization import prepare


@pytest.fixture
def capture_inputs():
    alignment = {"schema_version": "1.0", "source_kind": "synthetic_contract_fixture",
                 "verified": True, "verified_at": "2026-08-23T08:00:00Z", "method": "synthetic",
                 "source_reference": "synthetic fixture", "alignment_id": "alignment",
                 "input_frame": "map", "output_coordinate_system": "scene_local_yup",
                 "transform": {"translation_m": [0, 0, 0], "rotation_xyzw": [-2**-.5, 0, 0, 2**-.5]},
                 "quality": {"max_position_residual_m": 0, "acceptance_position_m": .1,
                             "max_heading_residual_deg": 0, "acceptance_heading_deg": 1,
                             "max_up_axis_error_deg": 0, "acceptance_up_axis_error_deg": 1}}
    files = {"config/scene_alignment.yaml": json.dumps(alignment).encode(),
             "config/camera_calibration.json": b"{}",
             "config/system.yaml": json.dumps({
                 "localization_adapter": {"ros__parameters": {"map_frame": "map", "base_frame": "base_link",
                     "odom_frame": "odom", "output_odom_topic": "/localization/odometry", "health_topic": "/safety/tf_healthy"}},
                 "health_monitor": {"ros__parameters": {"scout_odom_topic": "/odom"}}}).encode()}
    def digest(path):
        return hashlib.sha256(files[path]).hexdigest()
    files["alignment/camera_to_scene.json"] = json.dumps({"verified": True, "alignment_id": "alignment",
        "scene_alignment_sha256": digest("config/scene_alignment.yaml"), "base_frame": "base_link",
        "camera_frame": "camera_optical_frame", "base_from_camera": np.eye(4).tolist()}).encode()
    policy = {"schema_version": "1.0", "source_reference": "synthetic fixture",
              "scene_version_id": "scene-v1", "alignment_id": "alignment", "alignment_evidence_sha256": "a"*64,
              "camera_calibration_sha256": digest("config/camera_calibration.json"),
              "extrinsic_sha256": digest("alignment/camera_to_scene.json"),
              "scene_alignment_sha256": digest("config/scene_alignment.yaml"),
              "max_pose_gap_seconds": .5, "image_model_sigma_px": 1, "max_reprojection_rmse_px": 1,
              "ambiguity_margin_px": 1, "max_position_std_m": .1, "max_timing_displacement_m": .1,
              "max_linear_speed_mps": .2, "max_angular_speed_radps": .5,
              "uncertainty": {k: .01 for k in ("extrinsic_position_sigma_m", "extrinsic_rotation_sigma_rad",
                  "alignment_position_sigma_m", "alignment_rotation_sigma_rad")},
              "markers": {"1": {"physical_size_m": .1, "size_sigma_m": .001}}}
    files["config/instrument-localization.json"] = json.dumps(policy).encode()
    inventory = {p: digest(p) for p in files}
    manifest = NS(source_kind="synthetic_contract_fixture", scene_version=NS(scene_version_id="scene-v1"),
                  alignment=NS(alignment_id="alignment", evidence_sha256="a"*64),
                  started_at="2026-08-23T07:00:00Z", ended_at="2026-08-23T09:00:00Z")
    stamp = 1_787_472_000_000_000_000
    source = {"schema_version": "1.3", "pose_method": "latest_normalized_odometry",
              "pose_topic": "/localization/odometry", "motion_topic": "/odom", "health_topic": "/safety/tf_healthy",
              "localization_healthy": True, "frame_id": "camera_optical_frame",
              "image_header_stamp_ns": stamp, "pose_header_stamp_ns": stamp, "image_pose_gap_ns": 0,
              "pose": {"frame_id": "map", "child_frame_id": "base_link", "position": dict(x=0, y=0, z=0),
                       "orientation_xyzw": [0, 0, 0, 1], "covariance_6x6": (np.eye(6)*.001).ravel().tolist(),
                       "position_variance_m2": [.001]*3, "twist": [0]*6, "twist_covariance_6x6": [0]*36},
              "measured_motion": {"header_stamp_ns": stamp, "frame_id": "odom", "child_frame_id": "base_link",
                                  "twist": [0]*6, "twist_covariance_6x6": [0]*36}}
    return files, inventory, manifest, source


def test_normalized_and_legacy_metadata_keep_explicit_semantics(capture_inputs):
    files, inventory, manifest, source = capture_inputs
    context, gaps = prepare(files.__getitem__, inventory, manifest, source)
    assert context is not None and gaps == []
    legacy = copy.deepcopy(source)
    legacy.update(schema_version="1.2", pose_method="latest_measured_odometry")
    legacy.pop("measured_motion")
    assert prepare(files.__getitem__, inventory, manifest, legacy)[0] is not None


@pytest.mark.parametrize("change,reason", [
    ("health", "healthy localization"), ("child", "frames disagree"),
    ("motion", "measured stop"), ("stale", "motion is stale"),
    ("variance", "variance inconsistent"), ("covariance", "positive semidefinite"),
    ("method", "healthy localization"), ("topic", "system config disagree"),
])
def test_invalid_normalized_metadata_blocks_metric_localization(capture_inputs, change, reason):
    files, inventory, manifest, source = capture_inputs
    if change == "health": source["localization_healthy"] = False
    if change == "child": source["pose"]["child_frame_id"] = "body"
    if change == "motion": source["measured_motion"]["twist"][0] = .1
    if change == "stale": source["measured_motion"]["header_stamp_ns"] -= 600_000_000
    if change == "variance": source["pose"]["position_variance_m2"][0] = .01
    if change == "covariance": source["pose"]["covariance_6x6"][35] = -1
    if change == "method": source["pose_method"] = "latest_measured_odometry"
    if change == "topic": source["pose_topic"] = "/Odometry"
    context, gaps = prepare(files.__getitem__, inventory, manifest, source)
    assert context is None and len(gaps) == 1 and reason in gaps[0]
