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
#include <gtest/gtest.h>

#include <cmath>

#include "safety_mux/arbitration_core.hpp"

namespace safety_mux
{
namespace
{

ArbitrationInput healthy_input()
{
  ArbitrationInput input;
  input.now_monotonic_ns = 1'000'000'000LL;
  input.command_timeout_ms = 300;
  input.health_timeout_ms = 500;
  input.max_linear_mps = 0.2;
  input.max_angular_rps = 0.5;

  input.emergency_stop = {true, false, input.now_monotonic_ns};
  input.chassis_fault = {true, false, input.now_monotonic_ns};
  input.lidar_healthy = {true, true, input.now_monotonic_ns};
  input.tf_healthy = {true, true, input.now_monotonic_ns};
  input.hold_to_run_permit = {true, true, input.now_monotonic_ns};
  return input;
}

}  // namespace

TEST(ArbitrationCoreTest, EmergencyStopHasHighestPriority)
{
  auto input = healthy_input();
  input.teleop = {true, {0.1, 0.0}, input.now_monotonic_ns};
  input.emergency_stop = {true, true, input.now_monotonic_ns};

  const auto result = arbitrate(input);
  EXPECT_TRUE(result.is_zero);
  EXPECT_EQ(result.reason, "emergency_stop");
  EXPECT_TRUE(result.requires_rearm);
}

TEST(ArbitrationCoreTest, UnmonitoredEmergencyStopDoesNotRequireFakeHeartbeat)
{
  auto input = healthy_input();
  input.teleop = {true, {0.1, 0.0}, input.now_monotonic_ns};
  input.emergency_stop = {};
  input.require_emergency_stop_heartbeat = false;

  const auto result = arbitrate(input);
  EXPECT_FALSE(result.is_zero);
  EXPECT_EQ(result.reason, "teleop");
  EXPECT_FALSE(result.requires_rearm);
}

TEST(ArbitrationCoreTest, MissingHealthHeartbeatFailsClosed)
{
  auto input = healthy_input();
  input.teleop = {true, {0.1, 0.0}, input.now_monotonic_ns};
  input.lidar_healthy = {false, false, 0};

  const auto result = arbitrate(input);
  EXPECT_TRUE(result.is_zero);
  EXPECT_EQ(result.reason, "lidar_unhealthy");
}

TEST(ArbitrationCoreTest, CommandTimeoutReturnsZero)
{
  auto input = healthy_input();
  input.teleop.last_seen_monotonic_ns = input.now_monotonic_ns - 400LL * 1'000'000LL;

  const auto result = arbitrate(input);
  EXPECT_TRUE(result.is_zero);
  EXPECT_EQ(result.reason, "command_timeout");
}

TEST(ArbitrationCoreTest, TeleopOverridesAuto)
{
  auto input = healthy_input();
  input.teleop = {true, {0.15, 0.1}, input.now_monotonic_ns};
  input.auto_cmd = {true, {0.05, 0.0}, input.now_monotonic_ns};

  const auto result = arbitrate(input);
  EXPECT_FALSE(result.is_zero);
  EXPECT_EQ(result.reason, "teleop");
  EXPECT_NEAR(result.command.linear_x, 0.15, 1e-9);
}

TEST(ArbitrationCoreTest, ReleasedHoldToRunPermitBlocksFreshTeleop)
{
  auto input = healthy_input();
  input.teleop = {true, {0.15, 0.1}, input.now_monotonic_ns};
  input.hold_to_run_permit = {true, false, input.now_monotonic_ns};

  const auto result = arbitrate(input);
  EXPECT_TRUE(result.is_zero);
  EXPECT_EQ(result.reason, "hold_to_run_released");
}

TEST(ArbitrationCoreTest, MissingHoldToRunPermitFailsClosedForTeleop)
{
  auto input = healthy_input();
  input.teleop = {true, {0.15, 0.1}, input.now_monotonic_ns};
  input.hold_to_run_permit = {};

  const auto result = arbitrate(input);
  EXPECT_TRUE(result.is_zero);
  EXPECT_EQ(result.reason, "hold_to_run_missing_or_stale");
}

TEST(ArbitrationCoreTest, MotionRearmRequiresPermitRelease)
{
  auto input = healthy_input();
  input.teleop = {true, {0.15, 0.1}, input.now_monotonic_ns};
  input.motion_rearm_required = true;

  const auto blocked = arbitrate(input);
  EXPECT_TRUE(blocked.is_zero);
  EXPECT_EQ(blocked.reason, "motion_rearm_required");
  EXPECT_FALSE(blocked.rearm_acknowledged);

  input.hold_to_run_permit.value = false;
  const auto released = arbitrate(input);
  EXPECT_TRUE(released.is_zero);
  EXPECT_EQ(released.reason, "motion_rearmed");
  EXPECT_TRUE(released.rearm_acknowledged);
}

TEST(ArbitrationCoreTest, LimitsAreApplied)
{
  auto input = healthy_input();
  input.teleop = {true, {1.0, 2.0}, input.now_monotonic_ns};

  const auto result = arbitrate(input);
  EXPECT_NEAR(result.command.linear_x, 0.2, 1e-9);
  EXPECT_NEAR(result.command.angular_z, 0.5, 1e-9);
}

TEST(ArbitrationCoreTest, HardLinearLimitCannotBeRaisedByParameter)
{
  auto input = healthy_input();
  input.max_linear_mps = 1.0;
  input.teleop = {true, {1.0, 0.0}, input.now_monotonic_ns};

  const auto result = arbitrate(input);
  EXPECT_NEAR(result.command.linear_x, 0.2, 1e-9);
}

TEST(ArbitrationCoreTest, InvalidCommandFailsClosed)
{
  auto input = healthy_input();
  input.teleop = {true, {NAN, 0.0}, input.now_monotonic_ns};

  const auto result = arbitrate(input);
  EXPECT_TRUE(result.is_zero);
  EXPECT_EQ(result.reason, "invalid_command");
}

TEST(ArbitrationCoreTest, ObstacleStopsAllCommandSourcesWhenRequired)
{
  auto input = healthy_input();
  input.teleop = {true, {0.1, 0.0}, input.now_monotonic_ns};
  input.require_obstacle_clear = true;
  input.obstacle_clear = {true, false, input.now_monotonic_ns};

  const auto result = arbitrate(input);
  EXPECT_TRUE(result.is_zero);
  EXPECT_EQ(result.reason, "obstacle_stop");
}

TEST(ArbitrationCoreTest, MissingObstacleHeartbeatFailsClosedWhenRequired)
{
  auto input = healthy_input();
  input.teleop = {true, {0.1, 0.0}, input.now_monotonic_ns};
  input.require_obstacle_clear = true;

  const auto result = arbitrate(input);
  EXPECT_TRUE(result.is_zero);
  EXPECT_EQ(result.reason, "obstacle_monitor_missing_or_stale");
}

}  // namespace safety_mux
