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

#include <chrono>
#include <memory>
#include <string>

#include "camera_info_manager/camera_info_manager.hpp"
#include "diagnostic_msgs/msg/diagnostic_array.hpp"
#include "diagnostic_msgs/msg/diagnostic_status.hpp"
#include "diagnostic_msgs/msg/key_value.hpp"
#include "hik_camera_ros2/camera_backend.hpp"
#include "hik_camera_ros2/camera_info_validation.hpp"
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/camera_info.hpp"
#include "sensor_msgs/msg/image.hpp"

using namespace std::chrono_literals;

class HikCameraNode final : public rclcpp::Node
{
public:
  HikCameraNode()
  : Node("hik_camera_node")
  {
    config_.backend = declare_parameter<std::string>("backend", "mvs");
    config_.host_ip = declare_parameter<std::string>("host_ip", "");
    config_.camera_ip = declare_parameter<std::string>("camera_ip", "");
    config_.frame_id = declare_parameter<std::string>("frame_id", "camera_link");
    image_topic_ = declare_parameter<std::string>("image_topic", "/camera/image_raw");
    camera_info_topic_ = declare_parameter<std::string>("camera_info_topic", "/camera/camera_info");
    camera_name_ = declare_parameter<std::string>("camera_name", "hik_mv_cs050_10gc");
    config_.expected_model = declare_parameter<std::string>("camera_model", "");
    config_.expected_serial = declare_parameter<std::string>("camera_serial", "");
    lens_id_ = declare_parameter<std::string>("lens_id", "");
    calibration_url_ = declare_parameter<std::string>("calibration_url", "");
    config_.pixel_format = declare_parameter<std::string>("pixel_format", "BGR8");
    config_.sensor_width = declare_parameter<int>("sensor_width", 0);
    config_.sensor_height = declare_parameter<int>("sensor_height", 0);
    config_.width = declare_parameter<int>("width", 1280);
    config_.height = declare_parameter<int>("height", 1024);
    config_.offset_x = declare_parameter<int>("offset_x", 0);
    config_.offset_y = declare_parameter<int>("offset_y", 0);
    config_.frame_rate = declare_parameter<double>("frame_rate", 10.0);
    config_.exposure_auto = declare_parameter<bool>("exposure_auto", false);
    config_.exposure_time_us = declare_parameter<double>("exposure_time_us", 5000.0);
    config_.gain_auto = declare_parameter<bool>("gain_auto", false);
    config_.gain_db = declare_parameter<double>("gain_db", 0.0);
    config_.reconnect_interval_ms = declare_parameter<int>("reconnect_interval_ms", 2000);
    diagnostics_topic_ = declare_parameter<std::string>("diagnostics_topic", "/diagnostics");

    image_pub_ = create_publisher<sensor_msgs::msg::Image>(image_topic_, 10);
    info_pub_ = create_publisher<sensor_msgs::msg::CameraInfo>(camera_info_topic_, 10);
    diagnostics_pub_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(
      diagnostics_topic_,
      10);

    configure_camera_info();
    backend_ = hik_camera_ros2::create_camera_backend(config_.backend);
    try_open();

    const auto period = std::chrono::duration<double>(1.0 / std::max(config_.frame_rate, 1.0));
    grab_timer_ = create_wall_timer(
      std::chrono::duration_cast<std::chrono::nanoseconds>(period),
      std::bind(&HikCameraNode::grab_once, this));
    stats_timer_ = create_wall_timer(1s, std::bind(&HikCameraNode::publish_diagnostics, this));
  }

  ~HikCameraNode() override
  {
    if (backend_ != nullptr) {
      backend_->close();
    }
  }

private:
  void configure_camera_info()
  {
    if (calibration_url_.empty()) {
      calibration_state_ = "not_configured";
      calibration_reason_ = "calibration_url_empty";
      RCLCPP_WARN(
        get_logger(),
        "No calibration_url configured; CameraInfo will be published as uncalibrated");
      return;
    }

    camera_info_manager_ = std::make_unique<camera_info_manager::CameraInfoManager>(
      this, camera_name_, calibration_url_);
    if (!camera_info_manager_->validateURL(calibration_url_)) {
      calibration_state_ = "load_failed";
      calibration_reason_ = "calibration_url_invalid";
      RCLCPP_ERROR(get_logger(), "Invalid camera calibration URL: %s", calibration_url_.c_str());
      return;
    }
    if (!camera_info_manager_->loadCameraInfo(calibration_url_)) {
      calibration_state_ = "load_failed";
      calibration_reason_ = "calibration_load_failed";
      RCLCPP_ERROR(get_logger(), "Failed to load camera calibration: %s", calibration_url_.c_str());
      return;
    }

    calibrated_info_ = camera_info_manager_->getCameraInfo();
    const auto validation = hik_camera_ros2::validate_camera_info(
      calibrated_info_, calibrated_info_.width, calibrated_info_.height);
    if (!validation.valid) {
      calibration_state_ = "invalid";
      calibration_reason_ = validation.reason;
      RCLCPP_ERROR(
        get_logger(), "Loaded camera calibration is invalid: %s", validation.reason.c_str());
      return;
    }

    calibration_state_ = "loaded";
    calibration_reason_ = validation.reason;
    RCLCPP_INFO(
      get_logger(), "Loaded camera calibration '%s' (%ux%u)", calibration_url_.c_str(),
      calibrated_info_.width, calibrated_info_.height);
  }

  void try_open()
  {
    if (backend_ == nullptr) {
      backend_ = hik_camera_ros2::create_camera_backend(config_.backend);
    }
    if (backend_->open(config_)) {
      RCLCPP_INFO(
        get_logger(), "Camera backend '%s' connected to %s",
        backend_->name().c_str(), config_.camera_ip.c_str());
      return;
    }
    const auto stats = backend_->stats();
    RCLCPP_WARN(
      get_logger(), "Camera backend '%s' not connected: %s",
      backend_->name().c_str(), stats.last_error.c_str());
  }

  void grab_once()
  {
    if (backend_ == nullptr) {
      return;
    }

    hik_camera_ros2::FrameData frame;
    if (!backend_->grab(frame)) {
      const auto now = std::chrono::steady_clock::now();
      if (now - last_reconnect_attempt_ >
        std::chrono::milliseconds(config_.reconnect_interval_ms))
      {
        last_reconnect_attempt_ = now;
        backend_->close();
        try_open();
      }
      return;
    }

    sensor_msgs::msg::Image image;
    image.header.stamp = now();
    image.header.frame_id = config_.frame_id;
    image.height = static_cast<uint32_t>(frame.height);
    image.width = static_cast<uint32_t>(frame.width);
    last_image_width_ = image.width;
    last_image_height_ = image.height;
    image.encoding = frame.encoding;
    image.is_bigendian = 0;
    image.step = frame.height > 0 ?
      static_cast<sensor_msgs::msg::Image::_step_type>(frame.bytes.size() / frame.height) :
      static_cast<sensor_msgs::msg::Image::_step_type>(frame.bytes.size());
    image.data = std::move(frame.bytes);
    image_pub_->publish(image);

    sensor_msgs::msg::CameraInfo info;
    if (camera_info_manager_ != nullptr && calibration_state_ != "load_failed" &&
      calibration_state_ != "invalid")
    {
      const auto validation = hik_camera_ros2::validate_camera_info(
        calibrated_info_, image.width, image.height);
      if (validation.valid) {
        info = calibrated_info_;
        calibration_state_ = "ready";
      } else {
        calibration_state_ = "dimension_mismatch";
      }
      calibration_reason_ = validation.reason;
    }
    info.header = image.header;
    info.width = image.width;
    info.height = image.height;
    info_pub_->publish(info);

    ++frames_in_window_;
  }

  void publish_diagnostics()
  {
    if (backend_ == nullptr) {
      return;
    }

    const auto stats = backend_->stats();
    const double fps = static_cast<double>(frames_in_window_);
    frames_in_window_ = 0;

    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "hik_camera_ros2";
    status.hardware_id = stats.device_serial.empty() ? config_.camera_ip : stats.device_serial;
    if (!stats.connected) {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::ERROR;
      status.message = stats.state + (stats.last_error.empty() ? "" : ": " + stats.last_error);
    } else if (calibration_state_ == "not_configured") {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::WARN;
      status.message = "camera connected but calibration is not configured";
    } else if (calibration_state_ != "ready") {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::ERROR;
      status.message = "camera connected but calibration is unusable: " + calibration_reason_;
    } else {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::OK;
      status.message = stats.state;
    }
    status.values = {
      make_kv("backend", backend_->name()),
      make_kv("connected", stats.connected ? "true" : "false"),
      make_kv("expected_model", config_.expected_model),
      make_kv("device_model", stats.device_model),
      make_kv("expected_serial", config_.expected_serial),
      make_kv("device_serial", stats.device_serial),
      make_kv("lens_id", lens_id_),
      make_kv("sensor_width", std::to_string(stats.sensor_width)),
      make_kv("sensor_height", std::to_string(stats.sensor_height)),
      make_kv("observed_fps", std::to_string(fps)),
      make_kv("target_fps", std::to_string(config_.frame_rate)),
      make_kv("exposure_auto", config_.exposure_auto ? "true" : "false"),
      make_kv("exposure_time_us", std::to_string(config_.exposure_time_us)),
      make_kv("gain_auto", config_.gain_auto ? "true" : "false"),
      make_kv("gain_db", std::to_string(config_.gain_db)),
      make_kv("requested_width", std::to_string(config_.width)),
      make_kv("requested_height", std::to_string(config_.height)),
      make_kv("requested_offset_x", std::to_string(config_.offset_x)),
      make_kv("requested_offset_y", std::to_string(config_.offset_y)),
      make_kv("image_width", std::to_string(last_image_width_)),
      make_kv("image_height", std::to_string(last_image_height_)),
      make_kv("frames_published", std::to_string(stats.frames_published)),
      make_kv("frames_dropped", std::to_string(stats.frames_dropped)),
      make_kv("reconnect_count", std::to_string(stats.reconnect_count)),
      make_kv("camera_name", camera_name_),
      make_kv("calibration_url", calibration_url_),
      make_kv("calibration_state", calibration_state_),
      make_kv("calibration_reason", calibration_reason_),
      make_kv("camera_info_valid", calibration_state_ == "ready" ? "true" : "false"),
    };

    diagnostic_msgs::msg::DiagnosticArray array;
    array.header.stamp = now();
    array.status = {status};
    diagnostics_pub_->publish(array);
  }

  static diagnostic_msgs::msg::KeyValue make_kv(const std::string & key, const std::string & value)
  {
    diagnostic_msgs::msg::KeyValue item;
    item.key = key;
    item.value = value;
    return item;
  }

  builtin_interfaces::msg::Time now()
  {
    // NOTE: rclcpp 16.0.11 (Jetson apt) lacks Time::to_msg(); use conversion operator.
    return static_cast<builtin_interfaces::msg::Time>(this->get_clock()->now());
  }

  hik_camera_ros2::CameraConfig config_;
  std::string image_topic_;
  std::string camera_info_topic_;
  std::string diagnostics_topic_;
  std::string camera_name_;
  std::string lens_id_;
  std::string calibration_url_;
  std::string calibration_state_{"not_configured"};
  std::string calibration_reason_{"calibration_url_empty"};
  sensor_msgs::msg::CameraInfo calibrated_info_;
  std::unique_ptr<camera_info_manager::CameraInfoManager> camera_info_manager_;
  std::unique_ptr<hik_camera_ros2::CameraBackend> backend_;
  std::chrono::steady_clock::time_point last_reconnect_attempt_{std::chrono::steady_clock::now()};
  std::uint64_t frames_in_window_{0};
  std::uint32_t last_image_width_{0};
  std::uint32_t last_image_height_{0};

  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr image_pub_;
  rclcpp::Publisher<sensor_msgs::msg::CameraInfo>::SharedPtr info_pub_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diagnostics_pub_;
  rclcpp::TimerBase::SharedPtr grab_timer_;
  rclcpp::TimerBase::SharedPtr stats_timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<HikCameraNode>());
  rclcpp::shutdown();
  return 0;
}
