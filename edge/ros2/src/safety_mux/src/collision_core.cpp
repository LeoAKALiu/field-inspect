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
#include "safety_mux/collision_core.hpp"

#include <algorithm>
#include <cmath>

namespace safety_mux
{

CollisionRecoveryFilter::CollisionRecoveryFilter(std::size_t clear_confirmations)
: clear_confirmations_(clear_confirmations)
{
}

bool CollisionRecoveryFilter::update(CollisionSample sample)
{
  if (sample != CollisionSample::kClear || clear_confirmations_ == 0) {
    clear_streak_ = 0;
    blocked_ = true;
    return blocked_;
  }
  if (!blocked_) {
    return false;
  }
  ++clear_streak_;
  if (clear_streak_ >= clear_confirmations_) {
    clear_streak_ = 0;
    blocked_ = false;
  }
  return blocked_;
}

bool collision_zone_valid(const CollisionZone & zone)
{
  return std::isfinite(zone.min_x_m) && std::isfinite(zone.max_x_m) &&
         std::isfinite(zone.half_width_m) && std::isfinite(zone.min_z_m) &&
         std::isfinite(zone.max_z_m) && zone.min_x_m >= 0.0 &&
         zone.min_x_m < zone.max_x_m && zone.half_width_m > 0.0 &&
         zone.min_z_m < zone.max_z_m && zone.min_points > 0;
}

CollisionResult evaluate_collision_zone(
  const std::vector<Point3D> & points,
  const CollisionZone & zone)
{
  CollisionResult result;
  if (!collision_zone_valid(zone)) {
    result.obstacle = true;
    return result;
  }

  for (const auto & point : points) {
    if (!std::isfinite(point.x) || !std::isfinite(point.y) || !std::isfinite(point.z)) {
      continue;
    }
    if (point.x < zone.min_x_m || point.x > zone.max_x_m ||
      std::abs(point.y) > zone.half_width_m || point.z < zone.min_z_m ||
      point.z > zone.max_z_m)
    {
      continue;
    }
    ++result.points_in_zone;
    result.nearest_x_m = std::min(result.nearest_x_m, point.x);
  }
  result.obstacle = result.points_in_zone >= zone.min_points;
  return result;
}

}  // namespace safety_mux
