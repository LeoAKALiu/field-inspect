# Offline ROS 2 Humble test environment for the first-party packages.
#
# Target: Ubuntu 22.04 (jammy) / ROS 2 Humble on linux/arm64 (Apple Silicon Mac).
# Purpose: offline algorithm, node, launch, rosbag/MCAP, TF and point-cloud testing.
# This image deliberately contains NO Hikrobot MVS SDK, no Livox vendor source,
# no scout_ros2/ugv_sdk, no CAN tooling and no CUDA. It is not a Jetson image and
# must not be presented as sensor, CAN, vehicle or Jetson acceptance evidence.
#
# Build:  docker build --platform linux/arm64 -f docker/ros-humble-test.Dockerfile .
# Use:    ./scripts/test_in_ros_humble_container.sh

FROM ros:humble-ros-base-jammy

ENV DEBIAN_FRONTEND=noninteractive

# Exact build/test dependencies of the five first-party packages (see src/*/package.xml),
# minus workspace-internal packages and minus vendor-only `scout_msgs` (third_party/scout_ros2,
# not imported on the Mac). ros-base already provides rclpy, launch/launch_ros, tf2_ros,
# ament_index_python and the common message packages. Note: `libpcl-all-dev` in
# inspection_pointcloud_tools' package.xml is a rosdep key; the jammy deb name is libpcl-dev.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libpcl-dev \
    python3-colcon-common-extensions \
    python3-numpy \
    python3-pip \
    python3-pytest \
    python3-scipy \
    python3-setuptools \
    python3-shapely \
    python3-yaml \
    ros-humble-ament-cmake-gtest \
    ros-humble-ament-copyright \
    ros-humble-ament-flake8 \
    ros-humble-ament-lint-auto \
    ros-humble-ament-lint-common \
    ros-humble-ament-pep257 \
    ros-humble-camera-info-manager \
    ros-humble-eigen3-cmake-module \
    ros-humble-launch-testing-ros \
    ros-humble-nav2-msgs \
    ros-humble-pcl-conversions \
    ros-humble-rosbag2-cpp \
    ros-humble-rosbag2-py \
    ros-humble-rosbag2-storage-mcap \
    ros-humble-sensor-msgs-py \
    ros-humble-tf2-ros-py \
  && rm -rf /var/lib/apt/lists/*

# inspection_pipeline uses jsonschema.Draft202012Validator, i.e. jsonschema >= 4.
# Ubuntu 22.04's apt python3-jsonschema is 3.2.0, so install the modern release from
# PyPI (pure-Python + aarch64 wheels; same requirement as the Jetson workspace).
RUN python3 -m pip install --no-cache-dir 'jsonschema>=4.18,<5'

WORKDIR /ws

COPY docker/run_staged_tests.sh /opt/scout/run_staged_tests.sh
RUN chmod +x /opt/scout/run_staged_tests.sh

# Hermetic default DDS domain for offline container tests. Tests that need shared
# participants are expected to run entirely inside one container invocation.
ENV ROS_DOMAIN_ID=88
