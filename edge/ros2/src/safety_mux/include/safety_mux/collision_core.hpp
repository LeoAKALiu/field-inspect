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

#include <cstddef>
#include <limits>
#include <vector>

namespace safety_mux
{

struct Point3D
{
  double x{0.0};
  double y{0.0};
  double z{0.0};
};

struct CollisionZone
{
  double min_x_m{0.0};
  double max_x_m{0.0};
  double half_width_m{0.0};
  double min_z_m{0.0};
  double max_z_m{0.0};
  std::size_t min_points{0};
};

struct CollisionResult
{
  bool obstacle{false};
  std::size_t points_in_zone{0};
  double nearest_x_m{std::numeric_limits<double>::infinity()};
};

enum class CollisionSample
{
  kInvalid,
  kClear,
  kObstacle,
};

class CollisionRecoveryFilter
{
public:
  explicit CollisionRecoveryFilter(std::size_t clear_confirmations);

  // Returns true while motion must remain blocked.
  bool update(CollisionSample sample);

private:
  std::size_t clear_confirmations_{0};
  std::size_t clear_streak_{0};
  bool blocked_{true};
};

bool collision_zone_valid(const CollisionZone & zone);
CollisionResult evaluate_collision_zone(
  const std::vector<Point3D> & points,
  const CollisionZone & zone);

}  // namespace safety_mux
