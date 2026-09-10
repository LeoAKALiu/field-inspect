"""Metric square-marker estimates; conditional covariance is not a measured residual."""
import json
import math
from datetime import datetime, timezone, timedelta
from typing import Literal
from pydantic import Field
from .domain_contract import StrictModel
from .instruments import instant

import cv2
import numpy as np
import yaml

FILES = ("config/instrument-localization.json", "alignment/camera_to_scene.json", "config/scene_alignment.yaml")


class MarkerMeasurement(StrictModel):
    physical_size_m: float = Field(gt=0)
    size_sigma_m: float = Field(gt=0)


class TransformUncertainty(StrictModel):
    extrinsic_position_sigma_m: float = Field(gt=0)
    extrinsic_rotation_sigma_rad: float = Field(gt=0)
    alignment_position_sigma_m: float = Field(gt=0)
    alignment_rotation_sigma_rad: float = Field(gt=0)


class LocalizationPolicy(StrictModel):
    schema_version: Literal["1.0"]
    source_reference: str = Field(min_length=1, max_length=1000)
    scene_version_id: str = Field(min_length=1)
    alignment_id: str = Field(min_length=1)
    alignment_evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    camera_calibration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extrinsic_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scene_alignment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_pose_gap_seconds: float = Field(gt=0, le=0.5)
    image_model_sigma_px: float = Field(gt=0)
    max_reprojection_rmse_px: float = Field(gt=0)
    ambiguity_margin_px: float = Field(gt=0)
    max_position_std_m: float = Field(gt=0)
    max_timing_displacement_m: float = Field(gt=0)
    max_linear_speed_mps: float = Field(gt=0)
    max_angular_speed_radps: float = Field(gt=0)
    uncertainty: TransformUncertainty
    markers: dict[str, MarkerMeasurement] = Field(min_length=1, max_length=512)


def positive(value, field, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{field} must be a finite {'nonnegative' if allow_zero else 'positive'} number")
    return float(value)


def rigid(value):
    result = np.asarray(value, dtype=float)
    if (result.shape != (4,4) or not np.isfinite(result).all() or not np.allclose(result[3], [0,0,0,1], atol=1e-8)
            or not np.allclose(result[:3,:3].T @ result[:3,:3], np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(result[:3,:3]), 1, atol=1e-6)):
        raise ValueError("transform must be a rigid finite 4x4 matrix")
    return result


def pose_matrix(position, quaternion):
    q = np.asarray(quaternion, dtype=float)
    p = np.asarray(position, dtype=float)
    if q.shape != (4,) or p.shape != (3,) or not np.isfinite(q).all() or not np.isfinite(p).all() or abs(float(q @ q)-1) > 1e-4:
        raise ValueError("pose requires a finite position and normalized xyzw quaternion")
    x,y,z,w = q / np.linalg.norm(q)
    result = np.eye(4)
    result[:3,:3] = [[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                    [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                    [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]]
    result[:3,3] = p
    return rigid(result)


def skew(vector):
    x,y,z = vector
    return np.array([[0,-z,y],[z,0,-x],[-y,x,0]], dtype=float)


def prepare(read, inventory, manifest, source):
    gaps = ["localization:missing:"+path for path in FILES if path not in inventory]
    if not source.get("pose"):
        gaps.append("localization:complete_capture_pose_missing")
    if gaps:
        return None, gaps
    try:
        policy = LocalizationPolicy.model_validate_json(read(FILES[0])).model_dump()
        extrinsic = json.loads(read(FILES[1]))
        alignment = yaml.safe_load(read(FILES[2]))
        if not all(isinstance(v, dict) for v in (policy, extrinsic, alignment)):
            raise ValueError("localization inputs must be objects")
        if policy.get("schema_version") != "1.0" or not policy.get("source_reference"):
            raise ValueError("localization policy requires schema 1.0 and measured source_reference")
        for field, expected in {"scene_version_id": manifest.scene_version.scene_version_id,
                                "alignment_id": manifest.alignment.alignment_id,
                                "alignment_evidence_sha256": manifest.alignment.evidence_sha256,
                                "camera_calibration_sha256": inventory.get("config/camera_calibration.json"),
                                "extrinsic_sha256": inventory[FILES[1]], "scene_alignment_sha256": inventory[FILES[2]]}.items():
            if policy.get(field) != expected:
                raise ValueError(f"localization policy binding mismatch: {field}")
        if extrinsic.get("verified") is not True or alignment.get("verified") is not True:
            raise ValueError("verified extrinsic and alignment declarations required")
        if alignment.get("schema_version") != "1.0" or alignment.get("source_kind") not in {"onsite_verified", "synthetic_contract_fixture"}:
            raise ValueError("unsupported scene alignment schema/source")
        instant(alignment.get("verified_at", ""))
        if not all(isinstance(alignment.get(k), str) and alignment[k].strip() for k in ("method", "source_reference")):
            raise ValueError("scene alignment method/source reference required")
        quality = alignment["quality"]
        for measured, limit in (("max_position_residual_m", "acceptance_position_m"),
                                ("max_heading_residual_deg", "acceptance_heading_deg"),
                                ("max_up_axis_error_deg", "acceptance_up_axis_error_deg")):
            if positive(quality.get(measured), measured, allow_zero=True) > positive(quality.get(limit), limit):
                raise ValueError("scene alignment quality exceeds declared acceptance")
        if manifest.source_kind == "inspection_run" and alignment.get("source_kind") == "synthetic_contract_fixture":
            raise ValueError("synthetic alignment cannot locate a recorded observation")
        if (extrinsic.get("alignment_id") != manifest.alignment.alignment_id
                or alignment.get("alignment_id") != manifest.alignment.alignment_id
                or extrinsic.get("scene_alignment_sha256") != inventory[FILES[2]]
                or alignment.get("output_coordinate_system") != "scene_local_yup"):
            raise ValueError("extrinsic/scene alignment identity mismatch")
        pose = source["pose"]
        if (not pose.get("frame_id") or not pose.get("child_frame_id") or not source.get("frame_id")
                or pose["frame_id"] != alignment.get("input_frame")
                or pose["child_frame_id"] != extrinsic.get("base_frame")
                or source["frame_id"] != extrinsic.get("camera_frame")):
            raise ValueError("image/odometry/extrinsic frames disagree")
        if source.get("pose_method") != "latest_measured_odometry":
            raise ValueError("unsupported capture pose method")
        stamps = [source.get(field) for field in ("image_header_stamp_ns", "pose_header_stamp_ns")]
        if any(type(v) is not int or v <= 0 for v in stamps):
            raise ValueError("missing image or odometry timestamp")
        for stamp in stamps:
            seconds, nanos = divmod(stamp, 1000000000)
            captured = datetime.fromtimestamp(seconds, timezone.utc) + timedelta(microseconds=nanos//1000)
            if not instant(str(manifest.started_at)) <= captured <= instant(str(manifest.ended_at)):
                raise ValueError("image/odometry timestamp is outside run")
        if source.get("image_pose_gap_ns") != abs(stamps[0]-stamps[1]):
            raise ValueError("capture synchronization metadata is inconsistent")
        gap = abs(stamps[0]-stamps[1]) / 1e9
        if gap > positive(policy.get("max_pose_gap_seconds"), "max_pose_gap_seconds"):
            raise ValueError("image/odometry synchronization gap exceeds policy")
        # ROS pose covariance ordering is translation xyz then fixed-axis rotation xyz, in parent frame.
        covariance = np.asarray(pose.get("covariance_6x6"), dtype=float)
        if covariance.size != 36:
            raise ValueError("full odometry covariance_6x6 missing")
        covariance = covariance.reshape(6,6)
        if (not np.isfinite(covariance).all() or not np.allclose(covariance,covariance.T,atol=1e-9)
                or np.linalg.eigvalsh(covariance).min() < -1e-10 or np.trace(covariance) <= 0):
            raise ValueError("odometry covariance must be nonzero, symmetric and positive semidefinite")
        map_from_base = pose_matrix([pose["position"][k] for k in ("x","y","z")], pose["orientation_xyzw"])
        transform = alignment["transform"]
        scene_from_map = pose_matrix(transform["translation_m"], transform["rotation_xyzw"])
        mapped_up = scene_from_map[:3,:3] @ [0,0,1]
        up_error = math.degrees(math.acos(float(np.clip(mapped_up[1], -1, 1))))
        if up_error > quality["acceptance_up_axis_error_deg"]:
            raise ValueError("scene alignment must map map Z-up to scene Y-up")
        for field in ("image_model_sigma_px", "max_reprojection_rmse_px", "ambiguity_margin_px",
                      "max_position_std_m", "max_timing_displacement_m", "max_linear_speed_mps", "max_angular_speed_radps"):
            positive(policy.get(field), field)
        uncertainty = policy["uncertainty"]
        for field in ("extrinsic_position_sigma_m", "extrinsic_rotation_sigma_rad", "alignment_position_sigma_m", "alignment_rotation_sigma_rad"):
            positive(uncertainty.get(field), field)
        if not isinstance(policy.get("markers"), dict) or not policy["markers"]:
            raise ValueError("marker dimensions and measurement uncertainty are missing")
        return {"policy":policy, "base_from_camera":rigid(extrinsic["base_from_camera"]),
                "map_from_base":map_from_base, "scene_from_map":scene_from_map,
                "pose_covariance":covariance, "gap_seconds":gap}, []
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError, yaml.YAMLError, np.linalg.LinAlgError) as exc:
        return None, ["localization:invalid_input:"+str(exc)]


def locate(context, marker_id, corners, camera, distortion):
    if context is None:
        return {"status":"blocked", "reason":"localization_input_unavailable"}
    try:
        return _locate(context, marker_id, corners, camera, distortion)
    except (ValueError, KeyError, TypeError, cv2.error, np.linalg.LinAlgError) as exc:
        return {"status":"blocked", "reason":"localization_failed:"+str(exc)}


def _locate(ctx, marker_id, corners, camera, distortion):
    policy = ctx["policy"]
    marker = policy["markers"].get(str(marker_id))
    if not isinstance(marker, dict):
        raise ValueError("physical size missing for detected marker")
    length = positive(marker.get("physical_size_m"), "physical_size_m")
    sigma_size = positive(marker.get("size_sigma_m"), "size_sigma_m")
    half = length/2
    object_points = np.array([[-half,half,0],[half,half,0],[half,-half,0],[-half,-half,0]], dtype=np.float64)
    count, rotations, translations, _ = cv2.solvePnPGeneric(object_points, np.ascontiguousarray(corners,dtype=np.float64), camera, distortion, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    candidates = []
    for rvec,tvec in zip(rotations,translations):
        rotation,_ = cv2.Rodrigues(rvec)
        if not np.isfinite(rotation).all() or not np.isfinite(tvec).all() or np.min((object_points @ rotation.T + tvec.reshape(3))[:,2]) <= 0:
            continue
        projected,jacobian = cv2.projectPoints(object_points,rvec,tvec,camera,distortion)
        rmse = float(np.sqrt(np.mean(np.sum((projected.reshape(4,2)-corners)**2,axis=1))))
        if not math.isfinite(rmse):
            continue
        candidates.append({"rmse":rmse,"rotation":rotation,"translation":tvec.reshape(3),"jacobian":jacobian})
    candidates.sort(key=lambda item:item["rmse"])
    result = {"status":"blocked", "method":"ippe_square_conditional_covariance_v1", "solver_solution_count":int(count),
              "candidate_reprojection_rmse_px":[item["rmse"] for item in candidates]}
    if not candidates:
        return {**result,"reason":"no_positive_depth_solution"}
    if candidates[0]["rmse"] > policy["max_reprojection_rmse_px"]:
        return {**result,"reason":"reprojection_error_exceeds_policy"}
    if len(candidates)>1 and candidates[1]["rmse"]-candidates[0]["rmse"] <= policy["ambiguity_margin_px"]:
        return {**result,"reason":"ambiguous_planar_pose"}
    chosen = candidates[0]; translation = chosen["translation"]
    jacobian = chosen["jacobian"][:,:6]
    singular = np.linalg.svd(jacobian,compute_uv=False)
    if singular[-1] <= singular[0]*1e-10:
        return {**result,"reason":"ill_conditioned_pose_jacobian"}
    inverse = np.linalg.pinv(jacobian, rcond=1e-10)
    visual_cov = (policy["image_model_sigma_px"]**2 * inverse @ inverse.T)[3:6,3:6]
    visual_cov += np.outer(translation,translation)*(sigma_size/length)**2
    extrinsic = ctx["base_from_camera"]; mapping = ctx["map_from_base"]; scene = ctx["scene_from_map"]
    base_position = extrinsic[:3,:3] @ translation + extrinsic[:3,3]
    map_position = mapping[:3,:3] @ base_position + mapping[:3,3]
    position = scene[:3,:3] @ map_position + scene[:3,3]
    u = policy["uncertainty"]
    lever_camera = skew(extrinsic[:3,:3] @ translation)
    base_cov = extrinsic[:3,:3] @ visual_cov @ extrinsic[:3,:3].T + np.eye(3)*u["extrinsic_position_sigma_m"]**2 + lever_camera @ lever_camera.T*u["extrinsic_rotation_sigma_rad"]**2
    pose_jacobian = np.hstack([np.eye(3),-skew(mapping[:3,:3] @ base_position)])
    map_cov = mapping[:3,:3] @ base_cov @ mapping[:3,:3].T + pose_jacobian @ ctx["pose_covariance"] @ pose_jacobian.T
    lever_map = skew(scene[:3,:3] @ map_position)
    covariance = scene[:3,:3] @ map_cov @ scene[:3,:3].T + np.eye(3)*u["alignment_position_sigma_m"]**2 + lever_map @ lever_map.T*u["alignment_rotation_sigma_rad"]**2
    covariance = (covariance+covariance.T)/2
    max_std = float(np.sqrt(max(0,float(np.linalg.eigvalsh(covariance).max()))))
    timing_bound = ctx["gap_seconds"]*(policy["max_linear_speed_mps"]+policy["max_angular_speed_radps"]*float(np.linalg.norm(base_position)))
    if not np.isfinite(covariance).all() or not np.isfinite(position).all() or not math.isfinite(timing_bound):
        raise ValueError("non-finite metric estimate")
    result.update({"conditional_position_covariance_m2":covariance.tolist(),"conditional_max_position_std_m":max_std,
                   "timing_displacement_bound_m":timing_bound,"pose_gap_seconds":ctx["gap_seconds"],
                   "uncertainty_assumptions":"independent small Gaussian errors; exact intrinsics/distortion; fixed-axis parent-frame odometry covariance; declared speed bounds; not a calibrated coverage guarantee",
                   "residual_m":None})
    if max_std > policy["max_position_std_m"] or timing_bound > policy["max_timing_displacement_m"]:
        return {**result,"reason":"uncertainty_or_timing_exceeds_policy"}
    result.update({"status":"estimated_needs_review", "position":dict(zip(("x","y","z"),position.tolist())),
                   "reason":"metric_estimate_requires_field_review"})
    return result
