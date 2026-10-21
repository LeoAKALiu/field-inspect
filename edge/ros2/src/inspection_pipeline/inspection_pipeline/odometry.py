"""Odometry reference-point changes with full ROS pose/twist covariance."""
from copy import deepcopy

import numpy as np
from scipy.spatial.transform import Rotation


def covariance_matrix(values, *, nonzero=True):
    matrix = np.asarray(values, dtype=float)
    if matrix.size != 36:
        raise ValueError("covariance_requires_36_values")
    matrix = matrix.reshape(6, 6)
    if (not np.isfinite(matrix).all() or not np.allclose(matrix, matrix.T, rtol=0, atol=1e-9)
            or (nonzero and np.trace(matrix) <= 0)):
        raise ValueError("covariance_invalid")
    try:
        if np.linalg.eigvalsh(matrix).min() < -1e-10:
            raise ValueError("covariance_invalid")
    except np.linalg.LinAlgError as exc:
        raise ValueError("covariance_invalid") from exc
    return matrix


def _skew(vector):
    x, y, z = vector
    return np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])


def normalize_odometry(message, map_to_input, base_to_body, map_frame, base_frame):
    """Treat extrinsics as fixed; pose errors are fixed-axis in the parent frame.

    Twist is expressed in the child frame. Zero twist covariance remains unknown;
    station stop evidence must also come from measured chassis odometry.
    """
    result = deepcopy(message)
    pose = message.pose.pose
    q = np.array([getattr(pose.orientation, k) for k in ("x", "y", "z", "w")])
    p = np.array([getattr(pose.position, k) for k in ("x", "y", "z")])
    if not np.isfinite(p).all() or not np.isfinite(q).all() or abs(q @ q - 1) > 1e-4:
        raise ValueError("pose_invalid")
    for transform in (map_to_input, base_to_body):
        values = np.array([*transform.translation, *transform.rotation])
        if not np.isfinite(values).all() or abs(values[3:] @ values[3:] - 1) > 1e-4:
            raise ValueError("transform_invalid")
    parent = Rotation.from_quat(map_to_input.rotation)
    input_body = Rotation.from_quat(q)
    base_body = Rotation.from_quat(base_to_body.rotation)
    body_base = base_body.inv()
    body_offset = -body_base.apply(base_to_body.translation)
    map_offset = parent.apply(input_body.apply(body_offset))
    position = parent.apply(p) + map_to_input.translation + map_offset
    rotation = (parent * input_body * body_base).as_quat()

    jacobian = np.zeros((6, 6))
    jacobian[:3, :3] = jacobian[3:, 3:] = parent.as_matrix()
    jacobian[:3, 3:] = -_skew(map_offset) @ parent.as_matrix()
    pose_covariance = jacobian @ covariance_matrix(message.pose.covariance) @ jacobian.T

    twist = np.array([getattr(part, k) for part in
                      (message.twist.twist.linear, message.twist.twist.angular)
                      for k in ("x", "y", "z")])
    if not np.isfinite(twist).all():
        raise ValueError("twist_invalid")
    twist_jacobian = np.zeros((6, 6))
    twist_jacobian[:3, :3] = twist_jacobian[3:, 3:] = base_body.as_matrix()
    twist_jacobian[:3, 3:] = -base_body.as_matrix() @ _skew(body_offset)
    velocity = twist_jacobian @ twist
    twist_covariance = (twist_jacobian @ covariance_matrix(message.twist.covariance, nonzero=False)
                        @ twist_jacobian.T)
    if not np.isfinite(position).all() or not np.isfinite(velocity).all():
        raise ValueError("normalized_odometry_nonfinite")
    covariance_matrix(pose_covariance)
    covariance_matrix(twist_covariance, nonzero=False)
    result.header.frame_id = map_frame
    result.child_frame_id = base_frame
    for k, value in zip(("x", "y", "z"), position):
        setattr(result.pose.pose.position, k, float(value))
    for k, value in zip(("x", "y", "z", "w"), rotation):
        setattr(result.pose.pose.orientation, k, float(value))
    for part, values in ((result.twist.twist.linear, velocity[:3]),
                         (result.twist.twist.angular, velocity[3:])):
        for k, value in zip(("x", "y", "z"), values):
            setattr(part, k, float(value))
    result.pose.covariance = pose_covariance.ravel().tolist()
    result.twist.covariance = twist_covariance.ravel().tolist()
    return result
