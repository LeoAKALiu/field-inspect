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

#pragma once

#include <cmath>
#include <cstdint>
#include <string>

#include "sensor_msgs/msg/camera_info.hpp"

namespace hik_camera_ros2
{

struct CameraInfoValidation
{
  bool valid{false};
  std::string reason;
};

inline CameraInfoValidation validate_camera_info(
  const sensor_msgs::msg::CameraInfo & info,
  std::uint32_t image_width,
  std::uint32_t image_height)
{
  if (info.width == 0 || info.height == 0) {
    return {false, "calibration_dimensions_missing"};
  }
  if (info.width != image_width || info.height != image_height) {
    return {false, "calibration_dimensions_mismatch"};
  }
  if (info.distortion_model.empty()) {
    return {false, "distortion_model_missing"};
  }

  for (const double value : info.d) {
    if (!std::isfinite(value)) {
      return {false, "distortion_coefficients_non_finite"};
    }
  }
  for (const double value : info.k) {
    if (!std::isfinite(value)) {
      return {false, "camera_matrix_non_finite"};
    }
  }
  for (const double value : info.r) {
    if (!std::isfinite(value)) {
      return {false, "rectification_matrix_non_finite"};
    }
  }
  for (const double value : info.p) {
    if (!std::isfinite(value)) {
      return {false, "projection_matrix_non_finite"};
    }
  }

  if (info.k[0] <= 0.0 || info.k[4] <= 0.0 || info.k[8] == 0.0) {
    return {false, "camera_matrix_invalid"};
  }
  if (info.p[0] <= 0.0 || info.p[5] <= 0.0 || info.p[10] == 0.0) {
    return {false, "projection_matrix_invalid"};
  }

  return {true, "calibrated"};
}

}  // namespace hik_camera_ros2
