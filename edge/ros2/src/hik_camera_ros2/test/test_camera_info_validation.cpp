// Copyright 2026 SCOUT Mini Team
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <limits>

#include "gtest/gtest.h"
#include "hik_camera_ros2/camera_info_validation.hpp"
#include "sensor_msgs/msg/camera_info.hpp"

namespace
{

sensor_msgs::msg::CameraInfo valid_camera_info()
{
  sensor_msgs::msg::CameraInfo info;
  info.width = 1280;
  info.height = 1024;
  info.distortion_model = "plumb_bob";
  info.d = {-0.1, 0.01, 0.0, 0.0, 0.0};
  info.k = {900.0, 0.0, 640.0, 0.0, 900.0, 512.0, 0.0, 0.0, 1.0};
  info.r = {1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0};
  info.p = {900.0, 0.0, 640.0, 0.0, 0.0, 900.0, 512.0, 0.0, 0.0, 0.0, 1.0, 0.0};
  return info;
}

TEST(CameraInfoValidation, AcceptsFiniteCalibrationAtImageResolution)
{
  const auto result = hik_camera_ros2::validate_camera_info(valid_camera_info(), 1280, 1024);
  EXPECT_TRUE(result.valid);
  EXPECT_EQ(result.reason, "calibrated");
}

TEST(CameraInfoValidation, RejectsResolutionMismatch)
{
  const auto result = hik_camera_ros2::validate_camera_info(valid_camera_info(), 640, 512);
  EXPECT_FALSE(result.valid);
  EXPECT_EQ(result.reason, "calibration_dimensions_mismatch");
}

TEST(CameraInfoValidation, RejectsZeroFocalLength)
{
  auto info = valid_camera_info();
  info.k[0] = 0.0;
  const auto result = hik_camera_ros2::validate_camera_info(info, 1280, 1024);
  EXPECT_FALSE(result.valid);
  EXPECT_EQ(result.reason, "camera_matrix_invalid");
}

TEST(CameraInfoValidation, RejectsNonFiniteCoefficients)
{
  auto info = valid_camera_info();
  info.d[0] = std::numeric_limits<double>::quiet_NaN();
  const auto result = hik_camera_ros2::validate_camera_info(info, 1280, 1024);
  EXPECT_FALSE(result.valid);
  EXPECT_EQ(result.reason, "distortion_coefficients_non_finite");
}

}  // namespace
