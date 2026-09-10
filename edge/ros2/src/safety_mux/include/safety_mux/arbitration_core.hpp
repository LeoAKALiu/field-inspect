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
#include <string>

namespace safety_mux
{

struct Twist2D
{
  double linear_x{0.0};
  double angular_z{0.0};
};

struct BoolHeartbeat
{
  bool present{false};
  bool value{false};
  std::int64_t last_seen_monotonic_ns{0};
};

struct CommandHeartbeat
{
  bool present{false};
  Twist2D value{};
  std::int64_t last_seen_monotonic_ns{0};
};

struct ArbitrationInput
{
  std::int64_t now_monotonic_ns{0};
  int command_timeout_ms{300};
  int health_timeout_ms{500};
  double max_linear_mps{0.2};
  double max_angular_rps{0.5};
  CommandHeartbeat teleop{};
  CommandHeartbeat guarded{};
  CommandHeartbeat auto_cmd{};
  BoolHeartbeat emergency_stop{};
  BoolHeartbeat chassis_fault{};
  BoolHeartbeat lidar_healthy{};
  BoolHeartbeat tf_healthy{};
  BoolHeartbeat obstacle_clear{};
  BoolHeartbeat hold_to_run_permit{};
  bool require_emergency_stop_heartbeat{true};
  bool require_obstacle_clear{false};
  bool require_hold_to_run{true};
  bool motion_rearm_required{false};
};

struct ArbitrationOutput
{
  Twist2D command{};
  std::string reason{"zero_default"};
  bool is_zero{true};
  bool requires_rearm{false};
  bool rearm_acknowledged{false};
};

ArbitrationOutput arbitrate(const ArbitrationInput & input);

}  // namespace safety_mux
