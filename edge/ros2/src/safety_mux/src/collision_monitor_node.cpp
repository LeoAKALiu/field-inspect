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
#include <cmath>
#include <cstdint>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "diagnostic_msgs/msg/diagnostic_array.hpp"
#include "diagnostic_msgs/msg/diagnostic_status.hpp"
#include "diagnostic_msgs/msg/key_value.hpp"
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/point_cloud2.hpp"
#include "sensor_msgs/point_cloud2_iterator.hpp"
#include "safety_mux/collision_core.hpp"
#include "std_msgs/msg/bool.hpp"

using namespace std::chrono_literals;

namespace
{

std::int64_t monotonic_now_ns()
{
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch())
         .count();
}

bool point_cloud_layout_valid(
  const sensor_msgs::msg::PointCloud2 & cloud, std::string & error)
{
  if (cloud.is_bigendian) {
    error = "invalid point field layout: big-endian point data is unsupported";
    return false;
  }

  if (cloud.height == 0) {
    error = "invalid point field layout: point cloud height is zero";
    return false;
  }
  const auto minimum_row_step =
    static_cast<std::size_t>(cloud.point_step) * static_cast<std::size_t>(cloud.width);
  const auto minimum_data_size =
    static_cast<std::size_t>(cloud.row_step) * static_cast<std::size_t>(cloud.height);
  if (cloud.row_step > minimum_row_step) {
    error = "invalid point field layout: row padding is unsupported";
    return false;
  }
  if (cloud.point_step == 0 || cloud.row_step < minimum_row_step ||
    cloud.data.size() != minimum_data_size)
  {
    error = "invalid point field layout: inconsistent point cloud buffer";
    return false;
  }

  std::vector<std::pair<std::size_t, std::size_t>> xyz_ranges;
  for (const char * name : {"x", "y", "z"}) {
    const sensor_msgs::msg::PointField * match = nullptr;
    for (const auto & field : cloud.fields) {
      if (field.name != name) {
        continue;
      }
      if (match != nullptr) {
        error = std::string("invalid point field layout: duplicate ") + name;
        return false;
      }
      match = &field;
    }
    if (match == nullptr || match->datatype != sensor_msgs::msg::PointField::FLOAT32 ||
      match->count != 1 ||
      static_cast<std::size_t>(match->offset) + sizeof(float) > cloud.point_step)
    {
      error = std::string("invalid point field layout: ") + name + " must be FLOAT32[1]";
      return false;
    }
    xyz_ranges.emplace_back(
      static_cast<std::size_t>(match->offset),
      static_cast<std::size_t>(match->offset) + sizeof(float));
  }
  for (std::size_t index = 0; index < xyz_ranges.size(); ++index) {
    for (std::size_t other = index + 1; other < xyz_ranges.size(); ++other) {
      if (xyz_ranges[index].first < xyz_ranges[other].second &&
        xyz_ranges[other].first < xyz_ranges[index].second)
      {
        error = "invalid point field layout: XYZ fields overlap";
        return false;
      }
    }
  }
  return true;
}

}  // namespace

class CollisionMonitorNode final : public rclcpp::Node
{
public:
  CollisionMonitorNode()
  : Node("collision_monitor")
  {
    input_topic_ = declare_parameter<std::string>("input_topic");
    output_topic_ = declare_parameter<std::string>("obstacle_clear_topic");
    diagnostics_topic_ = declare_parameter<std::string>("diagnostics_topic");
    expected_frame_id_ = declare_parameter<std::string>("expected_frame_id");
    cloud_timeout_ms_ = declare_parameter<int>("cloud_timeout_ms");
    zone_.min_x_m = declare_parameter<double>("min_x_m");
    zone_.max_x_m = declare_parameter<double>("max_x_m");
    zone_.half_width_m = declare_parameter<double>("half_width_m");
    zone_.min_z_m = declare_parameter<double>("min_z_m");
    zone_.max_z_m = declare_parameter<double>("max_z_m");
    const int min_points = declare_parameter<int>("min_points");
    zone_.min_points = min_points > 0 ? static_cast<std::size_t>(min_points) : 0U;
    const int clear_confirmations = declare_parameter<int>("clear_confirmations");

    if (!safety_mux::collision_zone_valid(zone_) || cloud_timeout_ms_ <= 0 ||
      clear_confirmations <= 0)
    {
      throw std::invalid_argument("invalid collision monitor parameters");
    }
    recovery_filter_ = safety_mux::CollisionRecoveryFilter(
      static_cast<std::size_t>(clear_confirmations));

    clear_pub_ = create_publisher<std_msgs::msg::Bool>(output_topic_, 10);
    diagnostics_pub_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(
      diagnostics_topic_, 10);
    cloud_sub_ = create_subscription<sensor_msgs::msg::PointCloud2>(
      input_topic_, rclcpp::SensorDataQoS(),
      std::bind(&CollisionMonitorNode::onCloud, this, std::placeholders::_1));
    timer_ = create_wall_timer(100ms, std::bind(&CollisionMonitorNode::publishState, this));
  }

private:
  void onCloud(const sensor_msgs::msg::PointCloud2::SharedPtr msg)
  {
    frame_ok_ = msg->header.frame_id == expected_frame_id_;
    parse_ok_ = false;
    last_result_ = {};

    if (!frame_ok_) {
      last_error_ = "unexpected frame '" + msg->header.frame_id + "'";
      failClosedForInvalidCloud();
      return;
    }

    last_cloud_rx_ns_ = monotonic_now_ns();
    if (!point_cloud_layout_valid(*msg, last_error_)) {
      failClosedForInvalidCloud();
      return;
    }

    try {
      std::vector<safety_mux::Point3D> points;
      points.reserve(static_cast<std::size_t>(msg->width) * msg->height);
      if (msg->width > 0) {
        sensor_msgs::PointCloud2ConstIterator<float> x(*msg, "x");
        sensor_msgs::PointCloud2ConstIterator<float> y(*msg, "y");
        sensor_msgs::PointCloud2ConstIterator<float> z(*msg, "z");
        for (; x != x.end(); ++x, ++y, ++z) {
          points.push_back({*x, *y, *z});
        }
      }
      last_result_ = safety_mux::evaluate_collision_zone(points, zone_);
      parse_ok_ = true;
      last_error_.clear();
    } catch (const std::runtime_error & error) {
      last_error_ = error.what();
      failClosedForInvalidCloud();
      return;
    }

    obstacle_active_ = recovery_filter_.update(
      last_result_.obstacle ? safety_mux::CollisionSample::kObstacle :
      safety_mux::CollisionSample::kClear);
    publishState();
  }

  void failClosedForInvalidCloud()
  {
    obstacle_active_ = recovery_filter_.update(safety_mux::CollisionSample::kInvalid);
    publishState();
  }

  bool cloudFresh(std::int64_t now_ns) const
  {
    if (last_cloud_rx_ns_ <= 0) {
      return false;
    }
    return now_ns - last_cloud_rx_ns_ <=
           static_cast<std::int64_t>(cloud_timeout_ms_) * 1'000'000LL;
  }

  void publishState()
  {
    const auto now_ns = monotonic_now_ns();
    const bool fresh = cloudFresh(now_ns);
    if (!fresh) {
      obstacle_active_ = recovery_filter_.update(safety_mux::CollisionSample::kInvalid);
    }
    const bool clear = fresh && frame_ok_ && parse_ok_ && !obstacle_active_;
    clear_pub_->publish(std_msgs::msg::Bool().set__data(clear));

    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "safety/collision_monitor";
    status.hardware_id = expected_frame_id_;
    if (!fresh) {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::ERROR;
      status.message = "point cloud stale or missing";
    } else if (!frame_ok_ || !parse_ok_) {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::ERROR;
      status.message = last_error_;
    } else if (last_result_.obstacle) {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::WARN;
      status.message = "obstacle in stop zone";
    } else if (obstacle_active_) {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::WARN;
      status.message = "waiting for consecutive clear point clouds";
    } else {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::OK;
      status.message = "stop zone clear";
    }
    status.values = {
      diagnostic_msgs::msg::KeyValue().set__key("clear").set__value(clear ? "true" : "false"),
      diagnostic_msgs::msg::KeyValue()
      .set__key("points_in_zone")
      .set__value(std::to_string(last_result_.points_in_zone)),
      diagnostic_msgs::msg::KeyValue()
      .set__key("nearest_x_m")
      .set__value(
        std::isfinite(last_result_.nearest_x_m) ?
        std::to_string(last_result_.nearest_x_m) : "none")};

    diagnostic_msgs::msg::DiagnosticArray diagnostics;
    diagnostics.header.stamp = get_clock()->now();
    diagnostics.status = {status};
    diagnostics_pub_->publish(diagnostics);
  }

  std::string input_topic_;
  std::string output_topic_;
  std::string diagnostics_topic_;
  std::string expected_frame_id_;
  int cloud_timeout_ms_{0};
  safety_mux::CollisionZone zone_;
  safety_mux::CollisionRecoveryFilter recovery_filter_{0};
  safety_mux::CollisionResult last_result_;
  std::int64_t last_cloud_rx_ns_{0};
  bool obstacle_active_{true};
  bool frame_ok_{false};
  bool parse_ok_{false};
  std::string last_error_;

  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr clear_pub_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diagnostics_pub_;
  rclcpp::Subscription<sensor_msgs::msg::PointCloud2>::SharedPtr cloud_sub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<CollisionMonitorNode>());
  rclcpp::shutdown();
  return 0;
}
