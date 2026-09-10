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
#include <vector>

#include "safety_mux/collision_core.hpp"

namespace safety_mux
{
namespace
{

CollisionZone valid_zone()
{
  CollisionZone zone;
  zone.min_x_m = 0.15;
  zone.max_x_m = 0.90;
  zone.half_width_m = 0.45;
  zone.min_z_m = -0.50;
  zone.max_z_m = 0.55;
  zone.min_points = 5;
  return zone;
}

}  // namespace

TEST(CollisionCoreTest, DetectsEnoughPointsInsideStopZone)
{
  auto zone = valid_zone();
  zone.min_points = 3;
  const std::vector<Point3D> points = {
    {0.30, 0.00, 0.00},
    {0.35, 0.10, 0.05},
    {0.40, -0.10, -0.05},
    {1.50, 0.00, 0.00},
  };

  const auto result = evaluate_collision_zone(points, zone);
  EXPECT_TRUE(result.obstacle);
  EXPECT_EQ(result.points_in_zone, 3U);
  EXPECT_NEAR(result.nearest_x_m, 0.30, 1e-9);
}

TEST(CollisionCoreTest, IgnoresOutsideAndInvalidPoints)
{
  auto zone = valid_zone();
  zone.min_points = 2;
  const std::vector<Point3D> points = {
    {0.30, 1.00, 0.00},
    {0.30, 0.00, 2.00},
    {NAN, 0.00, 0.00},
    {0.25, 0.00, 0.00},
  };

  const auto result = evaluate_collision_zone(points, zone);
  EXPECT_FALSE(result.obstacle);
  EXPECT_EQ(result.points_in_zone, 1U);
}

TEST(CollisionCoreTest, InvalidZoneFailsClosed)
{
  auto zone = valid_zone();
  zone.min_x_m = 1.0;
  zone.max_x_m = 0.5;
  EXPECT_FALSE(collision_zone_valid(zone));
  EXPECT_TRUE(evaluate_collision_zone({}, zone).obstacle);
}

TEST(CollisionRecoveryFilterTest, InvalidSampleRestartsConsecutiveClearSequence)
{
  CollisionRecoveryFilter filter(3);

  EXPECT_TRUE(filter.update(CollisionSample::kObstacle));
  EXPECT_TRUE(filter.update(CollisionSample::kClear));
  EXPECT_TRUE(filter.update(CollisionSample::kClear));

  EXPECT_TRUE(filter.update(CollisionSample::kInvalid));
  EXPECT_TRUE(filter.update(CollisionSample::kClear));
  EXPECT_TRUE(filter.update(CollisionSample::kClear));
  EXPECT_FALSE(filter.update(CollisionSample::kClear));
}

}  // namespace safety_mux
