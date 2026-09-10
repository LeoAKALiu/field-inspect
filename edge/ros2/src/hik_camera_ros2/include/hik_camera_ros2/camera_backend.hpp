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

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace hik_camera_ros2
{

struct CameraConfig
{
  std::string backend{"mvs"};
  std::string host_ip;
  std::string camera_ip;
  std::string frame_id{"camera_link"};
  std::string expected_model;
  std::string expected_serial;
  std::string pixel_format{"BGR8"};
  int sensor_width{0};
  int sensor_height{0};
  int width{1280};
  int height{1024};
  int offset_x{0};
  int offset_y{0};
  double frame_rate{10.0};
  bool exposure_auto{false};
  double exposure_time_us{5000.0};
  bool gain_auto{false};
  double gain_db{0.0};
  int reconnect_interval_ms{2000};
};

struct FrameData
{
  std::vector<std::uint8_t> bytes;
  int width{0};
  int height{0};
  std::string encoding{"bgr8"};
};

struct CameraStats
{
  double observed_fps{0.0};
  std::uint64_t frames_published{0};
  std::uint64_t frames_dropped{0};
  std::uint64_t reconnect_count{0};
  bool connected{false};
  std::string state{"idle"};
  std::string last_error;
  std::string device_model;
  std::string device_serial;
  int sensor_width{0};
  int sensor_height{0};
};

class CameraBackend
{
public:
  virtual ~CameraBackend() = default;
  virtual bool open(const CameraConfig & config) = 0;
  virtual bool grab(FrameData & frame) = 0;
  virtual void close() = 0;
  virtual CameraStats stats() const = 0;
  virtual std::string name() const = 0;
};

std::unique_ptr<CameraBackend> create_camera_backend(const std::string & backend_name);

}  // namespace hik_camera_ros2
