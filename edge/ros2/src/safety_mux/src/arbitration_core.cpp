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
#include "safety_mux/arbitration_core.hpp"

#include <algorithm>
#include <cmath>

namespace safety_mux
{
namespace
{

constexpr std::int64_t kNanosecondsPerMillisecond = 1'000'000LL;
constexpr double kHardMaxLinearMps = 0.2;
constexpr double kHardMaxAngularRps = 0.5;

bool is_fresh(std::int64_t now_ns, std::int64_t last_seen_ns, int timeout_ms)
{
  if (last_seen_ns <= 0) {
    return false;
  }
  return (now_ns - last_seen_ns) <=
         static_cast<std::int64_t>(timeout_ms) * kNanosecondsPerMillisecond;
}

bool heartbeat_ok(
  const BoolHeartbeat & heartbeat, std::int64_t now_ns, int timeout_ms,
  bool expected_value)
{
  if (!heartbeat.present) {
    return false;
  }
  if (!is_fresh(now_ns, heartbeat.last_seen_monotonic_ns, timeout_ms)) {
    return false;
  }
  return heartbeat.value == expected_value;
}

bool twist_valid(const Twist2D & twist)
{
  return std::isfinite(twist.linear_x) && std::isfinite(twist.angular_z);
}

Twist2D clamp_twist(const Twist2D & twist, double max_linear, double max_angular)
{
  const double linear_limit = std::isfinite(max_linear) && max_linear > 0.0 ?
    std::min(max_linear, kHardMaxLinearMps) : 0.0;
  const double angular_limit = std::isfinite(max_angular) && max_angular > 0.0 ?
    std::min(max_angular, kHardMaxAngularRps) : 0.0;
  Twist2D clamped = twist;
  clamped.linear_x = std::clamp(clamped.linear_x, -linear_limit, linear_limit);
  clamped.angular_z = std::clamp(clamped.angular_z, -angular_limit, angular_limit);
  return clamped;
}

ArbitrationOutput zero_output(const char * reason, bool requires_rearm = false)
{
  ArbitrationOutput output;
  output.reason = reason;
  output.is_zero = true;
  output.requires_rearm = requires_rearm;
  return output;
}

const CommandHeartbeat * select_command_source(const ArbitrationInput & input)
{
  if (is_fresh(
      input.now_monotonic_ns, input.teleop.last_seen_monotonic_ns,
      input.command_timeout_ms))
  {
    return &input.teleop;
  }
  if (is_fresh(
      input.now_monotonic_ns, input.guarded.last_seen_monotonic_ns,
      input.command_timeout_ms))
  {
    return &input.guarded;
  }
  if (is_fresh(
      input.now_monotonic_ns, input.auto_cmd.last_seen_monotonic_ns,
      input.command_timeout_ms))
  {
    return &input.auto_cmd;
  }
  return nullptr;
}

}  // namespace

ArbitrationOutput arbitrate(const ArbitrationInput & input)
{
  if (input.emergency_stop.present &&
    is_fresh(
      input.now_monotonic_ns, input.emergency_stop.last_seen_monotonic_ns,
      input.health_timeout_ms) &&
    input.emergency_stop.value)
  {
    return zero_output("emergency_stop", true);
  }

  if (input.require_emergency_stop_heartbeat &&
    !heartbeat_ok(
      input.emergency_stop, input.now_monotonic_ns, input.health_timeout_ms, false))
  {
    return zero_output("emergency_stop_missing_or_stale", true);
  }

  if (input.chassis_fault.present &&
    is_fresh(
      input.now_monotonic_ns, input.chassis_fault.last_seen_monotonic_ns,
      input.health_timeout_ms) &&
    input.chassis_fault.value)
  {
    return zero_output("chassis_fault", true);
  }

  if (!heartbeat_ok(input.chassis_fault, input.now_monotonic_ns, input.health_timeout_ms, false)) {
    return zero_output("chassis_fault_missing_or_stale", true);
  }

  if (!heartbeat_ok(input.lidar_healthy, input.now_monotonic_ns, input.health_timeout_ms, true)) {
    return zero_output("lidar_unhealthy", true);
  }

  if (!heartbeat_ok(input.tf_healthy, input.now_monotonic_ns, input.health_timeout_ms, true)) {
    return zero_output("tf_unhealthy", true);
  }

  if (input.require_obstacle_clear) {
    if (input.obstacle_clear.present &&
      is_fresh(
        input.now_monotonic_ns, input.obstacle_clear.last_seen_monotonic_ns,
        input.health_timeout_ms) && !input.obstacle_clear.value)
    {
      return zero_output("obstacle_stop", true);
    }
    if (!heartbeat_ok(
        input.obstacle_clear, input.now_monotonic_ns, input.health_timeout_ms, true))
    {
      return zero_output("obstacle_monitor_missing_or_stale", true);
    }
  }

  if (input.motion_rearm_required) {
    if (heartbeat_ok(
        input.hold_to_run_permit, input.now_monotonic_ns,
        input.health_timeout_ms, false))
    {
      auto output = zero_output("motion_rearmed");
      output.rearm_acknowledged = true;
      return output;
    }
    return zero_output("motion_rearm_required");
  }

  const CommandHeartbeat * selected = select_command_source(input);
  if (selected == nullptr) {
    return zero_output("command_timeout");
  }

  if (!twist_valid(selected->value)) {
    return zero_output("invalid_command");
  }

  if (selected == &input.teleop && input.require_hold_to_run) {
    if (!input.hold_to_run_permit.present ||
      !is_fresh(
        input.now_monotonic_ns,
        input.hold_to_run_permit.last_seen_monotonic_ns,
        input.health_timeout_ms))
    {
      return zero_output("hold_to_run_missing_or_stale");
    }
    if (!input.hold_to_run_permit.value) {
      return zero_output("hold_to_run_released");
    }
  }

  ArbitrationOutput output;
  output.command = clamp_twist(selected->value, input.max_linear_mps, input.max_angular_rps);
  output.is_zero = output.command.linear_x == 0.0 && output.command.angular_z == 0.0;
  if (selected == &input.teleop) {
    output.reason = output.is_zero ? "teleop_zero" : "teleop";
  } else if (selected == &input.guarded) {
    output.reason = output.is_zero ? "guarded_zero" : "guarded";
  } else {
    output.reason = output.is_zero ? "auto_zero" : "auto";
  }
  return output;
}

}  // namespace safety_mux
