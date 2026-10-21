"""Reference-point normalization checks, independent of ROS transport."""
from copy import deepcopy
from types import SimpleNamespace as NS

import numpy as np
import pytest

from inspection_pipeline.odometry import covariance_matrix, normalize_odometry


def vector(values):
    return NS(**dict(zip(("x", "y", "z", "w"), values)))


def message():
    return NS(header=NS(stamp=NS(sec=10, nanosec=42), frame_id="camera_init"),
              child_frame_id="body",
              pose=NS(pose=NS(position=vector((3., 4., 5.)),
                              orientation=vector((0., 0., 2**-.5, 2**-.5))),
                      covariance=np.diag([.01, .02, .03, .04, .05, .06]).ravel().tolist()),
              twist=NS(twist=NS(linear=vector((1., 2., 3.)), angular=vector((0., 0., 2.))),
                       covariance=np.diag([.1, .2, .3, .4, .5, .6]).ravel().tolist()))


def test_nontrivial_parent_and_reference_point_preserve_cross_covariance():
    source = message()
    original = deepcopy(source)
    parent = NS(translation=(10., -2., 1.), rotation=(0., 0., 2**-.5, 2**-.5))
    extrinsic = NS(translation=(1., 0., 0.), rotation=parent.rotation)
    result = normalize_odometry(source, parent, extrinsic, "map", "base_link")
    assert source == original
    assert vars(result.header.stamp) == vars(source.header.stamp)
    assert (result.header.frame_id, result.child_frame_id) == ("map", "base_link")
    assert vars(result.pose.pose.position) == pytest.approx(dict(x=6, y=0, z=6))
    assert vars(result.pose.pose.orientation) == pytest.approx(dict(x=0, y=0, z=2**-.5, w=2**-.5))
    assert vars(result.twist.twist.linear) == pytest.approx(dict(x=-2, y=-1, z=3))
    assert vars(result.twist.twist.angular) == pytest.approx(dict(x=0, y=0, z=2))
    pose = covariance_matrix(result.pose.covariance)
    twist = covariance_matrix(result.twist.covariance)
    assert pose.diagonal() == pytest.approx([.08, .01, .08, .05, .04, .06])
    assert pose[0, 5] == pytest.approx(.06)
    assert pose[2, 3] == pytest.approx(-.05)
    assert twist.diagonal() == pytest.approx([.2, .7, .7, .5, .4, .6])
    assert twist[1, 5] == pytest.approx(-.6)
    assert twist[2, 4] == pytest.approx(.4)


@pytest.mark.parametrize("kind", ["zero", "negative", "asymmetric", "nan", "indefinite"])
def test_invalid_full_covariance_is_rejected(kind):
    matrix = np.eye(6)
    if kind == "zero":
        matrix[:] = 0
    elif kind == "negative":
        matrix[5, 5] = -1
    elif kind == "asymmetric":
        matrix[0, 1] = 1
    elif kind == "nan":
        matrix[0, 0] = float("nan")
    else:
        matrix[0, 1] = matrix[1, 0] = 2
    with pytest.raises(ValueError, match="covariance_invalid"):
        covariance_matrix(matrix.ravel())
