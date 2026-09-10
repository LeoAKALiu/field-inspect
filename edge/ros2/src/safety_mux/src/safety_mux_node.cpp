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
#include <atomic>
#include <chrono>
#include <csignal>
#include <functional>
#include <memory>
#include <string>
#include <thread>

#include "diagnostic_msgs/msg/diagnostic_array.hpp"
#include "diagnostic_msgs/msg/diagnostic_status.hpp"
#include "diagnostic_msgs/msg/key_value.hpp"
#include "geometry_msgs/msg/twist.hpp"
#include "rclcpp/rclcpp.hpp"
#include "safety_mux/arbitration_core.hpp"
#include "sensor_msgs/msg/joy.hpp"
#include "std_msgs/msg/bool.hpp"

using namespace std::chrono_literals;

namespace
{

safety_mux::Twist2D from_ros(const geometry_msgs::msg::Twist & msg)
{
  return {msg.linear.x, msg.angular.z};
}

geometry_msgs::msg::Twist to_ros(const safety_mux::Twist2D & twist)
{
  geometry_msgs::msg::Twist msg;
  msg.linear.x = twist.linear_x;
  msg.angular.z = twist.angular_z;
  return msg;
}

std::int64_t monotonic_now_ns()
{
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch())
         .count();
}

}  // namespace

class SafetyMuxNode final : public rclcpp::Node
{
public:
  SafetyMuxNode()
  : Node("safety_mux")
  {
    teleop_topic_ = declare_parameter<std::string>("teleop_topic", "/cmd_vel_teleop");
    auto_topic_ = declare_parameter<std::string>("auto_topic", "/cmd_vel_auto");
    guarded_topic_ = declare_parameter<std::string>("guarded_topic", "/cmd_vel_guarded");
    output_topic_ = declare_parameter<std::string>("output_topic", "/cmd_vel_safe");
    diagnostics_topic_ = declare_parameter<std::string>("diagnostics_topic");
    emergency_stop_topic_ = declare_parameter<std::string>(
      "emergency_stop_topic",
      "/safety/emergency_stop");
    chassis_fault_topic_ = declare_parameter<std::string>(
      "chassis_fault_topic",
      "/safety/chassis_fault");
    lidar_healthy_topic_ = declare_parameter<std::string>(
      "lidar_healthy_topic",
      "/safety/lidar_healthy");
    tf_healthy_topic_ = declare_parameter<std::string>("tf_healthy_topic", "/safety/tf_healthy");
    obstacle_clear_topic_ = declare_parameter<std::string>("obstacle_clear_topic");
    joy_topic_ = declare_parameter<std::string>("joy_topic", "/joy");
    require_obstacle_clear_ = declare_parameter<bool>("require_obstacle_clear");
    require_emergency_stop_heartbeat_ = declare_parameter<bool>(
      "require_emergency_stop_heartbeat", false);
    require_hold_to_run_ = declare_parameter<bool>("require_hold_to_run", true);
    teleop_enable_button_ = declare_parameter<int>("teleop_enable_button", 5);
    command_timeout_ms_ = declare_parameter<int>("command_timeout_ms", 300);
    health_timeout_ms_ = declare_parameter<int>("health_timeout_ms", 500);
    max_linear_mps_ = declare_parameter<double>("max_linear_mps", 0.2);
    max_angular_rps_ = declare_parameter<double>("max_angular_rps", 0.5);

    output_pub_ = create_publisher<geometry_msgs::msg::Twist>(output_topic_, 10);
    diagnostics_pub_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(
      diagnostics_topic_, 10);

    teleop_sub_ = create_subscription<geometry_msgs::msg::Twist>(
      teleop_topic_, 10, [this](const geometry_msgs::msg::Twist & msg) {
        teleop_.present = true;
        teleop_.value = from_ros(msg);
        teleop_.last_seen_monotonic_ns = monotonic_now_ns();
      });

    auto_sub_ = create_subscription<geometry_msgs::msg::Twist>(
      auto_topic_, 10, [this](const geometry_msgs::msg::Twist & msg) {
        auto_cmd_.present = true;
        auto_cmd_.value = from_ros(msg);
        auto_cmd_.last_seen_monotonic_ns = monotonic_now_ns();
      });

    guarded_sub_ = create_subscription<geometry_msgs::msg::Twist>(
      guarded_topic_, 10, [this](const geometry_msgs::msg::Twist & msg) {
        guarded_.present = true;
        guarded_.value = from_ros(msg);
        guarded_.last_seen_monotonic_ns = monotonic_now_ns();
      });

    emergency_stop_sub_ = create_subscription<std_msgs::msg::Bool>(
      emergency_stop_topic_, 10, [this](const std_msgs::msg::Bool & msg) {
        emergency_stop_.present = true;
        emergency_stop_.value = msg.data;
        emergency_stop_.last_seen_monotonic_ns = monotonic_now_ns();
      });

    chassis_fault_sub_ = create_subscription<std_msgs::msg::Bool>(
      chassis_fault_topic_, 10, [this](const std_msgs::msg::Bool & msg) {
        chassis_fault_.present = true;
        chassis_fault_.value = msg.data;
        chassis_fault_.last_seen_monotonic_ns = monotonic_now_ns();
      });

    lidar_healthy_sub_ = create_subscription<std_msgs::msg::Bool>(
      lidar_healthy_topic_, 10, [this](const std_msgs::msg::Bool & msg) {
        lidar_healthy_.present = true;
        lidar_healthy_.value = msg.data;
        lidar_healthy_.last_seen_monotonic_ns = monotonic_now_ns();
      });

    tf_healthy_sub_ = create_subscription<std_msgs::msg::Bool>(
      tf_healthy_topic_, 10, [this](const std_msgs::msg::Bool & msg) {
        tf_healthy_.present = true;
        tf_healthy_.value = msg.data;
        tf_healthy_.last_seen_monotonic_ns = monotonic_now_ns();
      });

    obstacle_clear_sub_ = create_subscription<std_msgs::msg::Bool>(
      obstacle_clear_topic_, 10, [this](const std_msgs::msg::Bool & msg) {
        obstacle_clear_.present = true;
        obstacle_clear_.value = msg.data;
        obstacle_clear_.last_seen_monotonic_ns = monotonic_now_ns();
      });

    joy_sub_ = create_subscription<sensor_msgs::msg::Joy>(
      joy_topic_, 10, [this](const sensor_msgs::msg::Joy & msg) {
        hold_to_run_permit_.present = true;
        hold_to_run_permit_.value =
        teleop_enable_button_ >= 0 &&
        static_cast<std::size_t>(teleop_enable_button_) < msg.buttons.size() &&
        msg.buttons[static_cast<std::size_t>(teleop_enable_button_)] != 0;
        hold_to_run_permit_.last_seen_monotonic_ns = monotonic_now_ns();
      });

    timer_ = create_wall_timer(50ms, std::bind(&SafetyMuxNode::publishSafeCommand, this));
  }

private:
  void publishSafeCommand()
  {
    safety_mux::ArbitrationInput input;
    input.now_monotonic_ns = monotonic_now_ns();
    input.command_timeout_ms = command_timeout_ms_;
    input.health_timeout_ms = health_timeout_ms_;
    input.max_linear_mps = max_linear_mps_;
    input.max_angular_rps = max_angular_rps_;
    input.teleop = teleop_;
    input.guarded = guarded_;
    input.auto_cmd = auto_cmd_;
    input.emergency_stop = emergency_stop_;
    input.chassis_fault = chassis_fault_;
    input.lidar_healthy = lidar_healthy_;
    input.tf_healthy = tf_healthy_;
    input.obstacle_clear = obstacle_clear_;
    input.hold_to_run_permit = hold_to_run_permit_;
    input.require_emergency_stop_heartbeat = require_emergency_stop_heartbeat_;
    input.require_obstacle_clear = require_obstacle_clear_;
    input.require_hold_to_run = require_hold_to_run_;
    input.motion_rearm_required = motion_rearm_required_;

    auto result = safety_mux::arbitrate(input);
    if (result.rearm_acknowledged) {
      teleop_ = {};
      guarded_ = {};
      auto_cmd_ = {};
      motion_rearm_required_ = false;
    } else if (result.requires_rearm) {
      motion_rearm_required_ = true;
    }
    if (result.reason != last_reason_) {
      RCLCPP_INFO(get_logger(), "Arbitration state: %s", result.reason.c_str());
      last_reason_ = result.reason;
    }
    output_pub_->publish(to_ros(result.command));

    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "safety/safety_mux";
    status.hardware_id = "software";
    status.level = result.is_zero ? diagnostic_msgs::msg::DiagnosticStatus::WARN :
      diagnostic_msgs::msg::DiagnosticStatus::OK;
    status.message = result.reason;
    status.values = {
      diagnostic_msgs::msg::KeyValue().set__key("reason").set__value(result.reason),
      diagnostic_msgs::msg::KeyValue()
      .set__key("safe_command_zero")
      .set__value(result.is_zero ? "true" : "false"),
      diagnostic_msgs::msg::KeyValue()
      .set__key("hold_to_run_present")
      .set__value(hold_to_run_permit_.present ? "true" : "false"),
      diagnostic_msgs::msg::KeyValue()
      .set__key("hold_to_run_value")
      .set__value(hold_to_run_permit_.value ? "true" : "false"),
      diagnostic_msgs::msg::KeyValue()
      .set__key("motion_rearm_required")
      .set__value(motion_rearm_required_ ? "true" : "false"),
      diagnostic_msgs::msg::KeyValue()
      .set__key("emergency_stop_monitored")
      .set__value(require_emergency_stop_heartbeat_ ? "true" : "false")};
    diagnostic_msgs::msg::DiagnosticArray diagnostics;
    diagnostics.header.stamp = get_clock()->now();
    diagnostics.status = {status};
    diagnostics_pub_->publish(diagnostics);
  }

  std::string teleop_topic_;
  std::string auto_topic_;
  std::string guarded_topic_;
  std::string output_topic_;
  std::string diagnostics_topic_;
  std::string emergency_stop_topic_;
  std::string chassis_fault_topic_;
  std::string lidar_healthy_topic_;
  std::string tf_healthy_topic_;
  std::string obstacle_clear_topic_;
  std::string joy_topic_;
  bool require_obstacle_clear_{false};
  bool require_emergency_stop_heartbeat_{false};
  bool require_hold_to_run_{true};
  bool motion_rearm_required_{false};
  int teleop_enable_button_{5};
  int command_timeout_ms_{300};
  int health_timeout_ms_{500};
  double max_linear_mps_{0.2};
  double max_angular_rps_{0.5};

  safety_mux::CommandHeartbeat teleop_;
  safety_mux::CommandHeartbeat guarded_;
  safety_mux::CommandHeartbeat auto_cmd_;
  safety_mux::BoolHeartbeat emergency_stop_;
  safety_mux::BoolHeartbeat chassis_fault_;
  safety_mux::BoolHeartbeat lidar_healthy_;
  safety_mux::BoolHeartbeat tf_healthy_;
  safety_mux::BoolHeartbeat obstacle_clear_;
  safety_mux::BoolHeartbeat hold_to_run_permit_;
  std::string last_reason_{"unset"};

  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr output_pub_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diagnostics_pub_;
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr teleop_sub_;
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr auto_sub_;
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr guarded_sub_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr emergency_stop_sub_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr chassis_fault_sub_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr lidar_healthy_sub_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr tf_healthy_sub_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr obstacle_clear_sub_;
  rclcpp::Subscription<sensor_msgs::msg::Joy>::SharedPtr joy_sub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char ** argv)
{
  // rclcpp 16.0.x calls shutdown() from its POSIX signal handler, which is not
  // async-signal-safe and intermittently aborts (SIGABRT) on teardown when
  // launch delivers SIGINT more than once. Take over SIGINT: only set a flag
  // in the handler and shut down from normal context in the main loop.
  // Install the handler before rclcpp::init so the default-disposition window
  // stays minimal (DDS discovery inside init can take a while).
  static std::atomic_bool sigint_seen{false};
  std::signal(SIGINT, [](int) {sigint_seen = true;});

  rclcpp::InitOptions init_options;
  init_options.shutdown_on_signal = false;
  rclcpp::init(argc, argv, init_options);

  auto node = std::make_shared<SafetyMuxNode>();
  while (rclcpp::ok() && !sigint_seen) {
    rclcpp::spin_some(node);
    std::this_thread::sleep_for(10ms);
  }
  node.reset();
  rclcpp::shutdown();
  return 0;
}
