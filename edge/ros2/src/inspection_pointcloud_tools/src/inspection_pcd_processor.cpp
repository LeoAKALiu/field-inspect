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

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "Eigen/Geometry"
#include "pcl/common/common.h"
#include "pcl/common/io.h"
#include "pcl/common/transforms.h"
#include "pcl/filters/voxel_grid.h"
#include "pcl/io/pcd_io.h"
#include "pcl/point_cloud.h"
#include "pcl/point_types.h"
#include "pcl_conversions/pcl_conversions.h"
#include "rclcpp/serialization.hpp"
#include "rclcpp/serialized_message.hpp"
#include "rosbag2_cpp/reader.hpp"
#include "rosbag2_storage/storage_filter.hpp"
#include "rosbag2_storage/storage_options.hpp"
#include "sensor_msgs/msg/point_cloud2.hpp"
#include "sensor_msgs/msg/point_field.hpp"

namespace
{

using Point = pcl::PointXYZI;
using Cloud = pcl::PointCloud<Point>;
using PointCloud2 = sensor_msgs::msg::PointCloud2;
using PointField = sensor_msgs::msg::PointField;

struct Options
{
  std::string bag_uri;
  std::string storage_id;
  std::string topic;
  std::string expected_frame;
  std::filesystem::path output_directory;
  std::uint64_t chunk_duration_ns;
  float voxel_size_m;
  std::uint64_t max_input_points_per_message;
  std::uint64_t max_input_points_per_chunk;
  std::uint64_t max_output_points_per_chunk;
  std::uint64_t max_chunks;
  std::uint64_t max_duration_ns;
  float max_abs_coordinate_m;
  Eigen::Vector3f translation;
  Eigen::Quaternionf rotation;
};

struct Chunk
{
  std::uint64_t sequence = 0;
  std::uint64_t window_index = 0;
  std::uint64_t first_header_stamp_ns = 0;
  std::uint64_t last_header_stamp_ns = 0;
  std::uint64_t first_bag_stamp_ns = 0;
  std::uint64_t last_bag_stamp_ns = 0;
  std::uint64_t message_count = 0;
  std::uint64_t input_point_count = 0;
  Cloud points;
};

std::uint64_t parse_positive_integer(const char * value, const char * field)
{
  std::size_t consumed = 0;
  std::uint64_t parsed = 0;
  try {
    parsed = std::stoull(value, &consumed);
  } catch (const std::exception &) {
    throw std::invalid_argument(std::string(field) + " must be a positive integer");
  }
  if (consumed != std::string(value).size() || parsed == 0) {
    throw std::invalid_argument(std::string(field) + " must be a positive integer");
  }
  return static_cast<std::uint64_t>(parsed);
}

float parse_finite_float(const char * value, const char * field)
{
  std::size_t consumed = 0;
  float parsed = 0.0F;
  try {
    parsed = std::stof(value, &consumed);
  } catch (const std::exception &) {
    throw std::invalid_argument(std::string(field) + " must be finite");
  }
  if (consumed != std::string(value).size() || !std::isfinite(parsed)) {
    throw std::invalid_argument(std::string(field) + " must be finite");
  }
  return parsed;
}

Options parse_options(int argc, char ** argv)
{
  if (argc != 21) {
    throw std::invalid_argument(
            "usage: inspection_pcd_processor BAG STORAGE TOPIC FRAME OUTPUT_DIR "
            "CHUNK_NS VOXEL MAX_MESSAGE MAX_CHUNK_INPUT MAX_CHUNK_OUTPUT MAX_CHUNKS "
            "MAX_DURATION_NS MAX_ABS_COORD TX TY TZ QX QY QZ QW");
  }
  Options options{
    argv[1],
    argv[2],
    argv[3],
    argv[4],
    argv[5],
    parse_positive_integer(argv[6], "chunk_duration_ns"),
    parse_finite_float(argv[7], "voxel_size_m"),
    parse_positive_integer(argv[8], "max_input_points_per_message"),
    parse_positive_integer(argv[9], "max_input_points_per_chunk"),
    parse_positive_integer(argv[10], "max_output_points_per_chunk"),
    parse_positive_integer(argv[11], "max_chunks"),
    parse_positive_integer(argv[12], "max_duration_ns"),
    parse_finite_float(argv[13], "max_abs_coordinate_m"),
    Eigen::Vector3f(
      parse_finite_float(argv[14], "translation_x"),
      parse_finite_float(argv[15], "translation_y"),
      parse_finite_float(argv[16], "translation_z")),
    Eigen::Quaternionf(
      parse_finite_float(argv[20], "rotation_w"),
      parse_finite_float(argv[17], "rotation_x"),
      parse_finite_float(argv[18], "rotation_y"),
      parse_finite_float(argv[19], "rotation_z"))};
  if (options.bag_uri.empty() || options.storage_id.empty() || options.topic.empty() ||
    options.expected_frame.empty())
  {
    throw std::invalid_argument("bag, storage, topic and frame must not be empty");
  }
  if (options.voxel_size_m <= 0.0F || options.max_abs_coordinate_m <= 0.0F) {
    throw std::invalid_argument("voxel size and coordinate limit must be positive");
  }
  if (std::abs(options.rotation.norm() - 1.0F) > 1.0e-4F) {
    throw std::invalid_argument("rotation quaternion must be normalized");
  }
  if (!std::filesystem::is_directory(options.output_directory) ||
    !std::filesystem::is_empty(options.output_directory))
  {
    throw std::invalid_argument("output directory must exist and be empty");
  }
  return options;
}

std::uint64_t header_stamp_ns(const PointCloud2 & message)
{
  if (message.header.stamp.sec < 0) {
    throw std::runtime_error("point cloud header timestamp must not be negative");
  }
  return static_cast<std::uint64_t>(message.header.stamp.sec) * 1000000000ULL +
         static_cast<std::uint64_t>(message.header.stamp.nanosec);
}

void require_xyzi_fields(const PointCloud2 & message)
{
  for (const char * required : {"x", "y", "z", "intensity"}) {
    const auto iterator = std::find_if(
      message.fields.begin(), message.fields.end(),
      [required](const PointField & field) {return field.name == required;});
    if (iterator == message.fields.end() || iterator->datatype != PointField::FLOAT32 ||
      iterator->count != 1)
    {
      throw std::runtime_error(
              std::string("point cloud requires FLOAT32 field ") + required);
    }
  }
  const std::uint64_t expected_bytes =
    static_cast<std::uint64_t>(message.row_step) * message.height;
  const std::uint64_t minimum_row_bytes =
    static_cast<std::uint64_t>(message.point_step) * message.width;
  if (message.point_step < 16 || message.row_step < minimum_row_bytes ||
    message.data.size() != expected_bytes)
  {
    throw std::runtime_error("point cloud byte layout is inconsistent");
  }
}

void require_bounded_point(const Point & point, float maximum)
{
  if (!pcl::isFinite(point) || !std::isfinite(point.intensity)) {
    throw std::runtime_error("point cloud contains NaN or Inf");
  }
  if (std::abs(point.x) > maximum || std::abs(point.y) > maximum ||
    std::abs(point.z) > maximum)
  {
    throw std::runtime_error("point cloud exceeds max_abs_coordinate_m");
  }
}

std::string chunk_name(std::uint64_t sequence)
{
  std::ostringstream stream;
  stream << std::setfill('0') << std::setw(6) << sequence << ".pcd";
  return stream.str();
}

void flush_chunk(const Options & options, Chunk & chunk)
{
  if (chunk.message_count == 0 || chunk.points.empty()) {
    throw std::runtime_error("cannot flush an empty point-cloud chunk");
  }
  auto input = Cloud::Ptr(new Cloud(std::move(chunk.points)));
  input->width = static_cast<std::uint32_t>(input->size());
  input->height = 1;
  input->is_dense = true;
  pcl::VoxelGrid<Point> voxel_grid;
  voxel_grid.setInputCloud(input);
  voxel_grid.setLeafSize(
    options.voxel_size_m, options.voxel_size_m, options.voxel_size_m);
  voxel_grid.setDownsampleAllData(true);
  Cloud output;
  voxel_grid.filter(output);
  if (output.empty()) {
    throw std::runtime_error("voxel filtering produced an empty point-cloud chunk");
  }
  if (output.size() > options.max_output_points_per_chunk) {
    throw std::runtime_error("point-cloud chunk exceeds max_output_points_per_chunk");
  }
  Point minimum;
  Point maximum;
  pcl::getMinMax3D(output, minimum, maximum);
  const auto path = options.output_directory / chunk_name(chunk.sequence);
  pcl::PCDWriter writer;
  if (writer.writeBinary(path.string(), output) != 0) {
    throw std::runtime_error("PCL failed to write binary PCD chunk");
  }
  std::cout << std::setprecision(std::numeric_limits<float>::max_digits10);
  std::cout << "chunk=" << chunk.sequence << '\t' << chunk.window_index << '\t'
            << chunk.first_header_stamp_ns << '\t' << chunk.last_header_stamp_ns << '\t'
            << chunk.first_bag_stamp_ns << '\t' << chunk.last_bag_stamp_ns << '\t'
            << chunk.message_count << '\t' << chunk.input_point_count << '\t'
            << output.size() << '\t' << minimum.x << '\t' << minimum.y << '\t'
            << minimum.z << '\t' << maximum.x << '\t' << maximum.y << '\t'
            << maximum.z << '\n';
}

int process(const Options & options)
{
  rosbag2_cpp::Reader reader;
  rosbag2_storage::StorageOptions storage_options;
  storage_options.uri = options.bag_uri;
  storage_options.storage_id = options.storage_id;
  reader.open(storage_options);
  const auto topics = reader.get_all_topics_and_types();
  const auto topic = std::find_if(
    topics.begin(), topics.end(),
    [&options](const auto & metadata) {return metadata.name == options.topic;});
  if (topic == topics.end() || topic->type != "sensor_msgs/msg/PointCloud2") {
    throw std::runtime_error(
            options.topic + " must exist with type sensor_msgs/msg/PointCloud2");
  }
  rosbag2_storage::StorageFilter filter;
  filter.topics = {options.topic};
  reader.set_filter(filter);

  const Eigen::Affine3f transform =
    Eigen::Translation3f(options.translation) * options.rotation;
  rclcpp::Serialization<PointCloud2> serialization;
  Chunk chunk;
  std::uint64_t total_message_count = 0;
  std::uint64_t total_input_point_count = 0;
  std::uint64_t first_header_stamp_ns = 0;
  std::uint64_t last_header_stamp_ns = 0;
  std::uint64_t first_bag_stamp_ns = 0;
  std::uint64_t last_bag_stamp_ns = 0;
  std::uint64_t emitted_chunks = 0;

  while (reader.has_next()) {
    const auto bag_message = reader.read_next();
    if (bag_message->time_stamp <= 0 ||
      (total_message_count > 0 &&
      static_cast<std::uint64_t>(bag_message->time_stamp) < last_bag_stamp_ns))
    {
      throw std::runtime_error("bag timestamps must be positive and nondecreasing");
    }
    rclcpp::SerializedMessage serialized(*bag_message->serialized_data);
    PointCloud2 message;
    serialization.deserialize_message(&serialized, &message);
    const std::uint64_t stamp_ns = header_stamp_ns(message);
    if (stamp_ns == 0 || (total_message_count > 0 && stamp_ns < last_header_stamp_ns)) {
      throw std::runtime_error("header timestamps must be positive and nondecreasing");
    }
    if (message.header.frame_id != options.expected_frame) {
      throw std::runtime_error(
              "point cloud frame " + message.header.frame_id + " does not match " +
              options.expected_frame);
    }
    require_xyzi_fields(message);
    const std::uint64_t point_count =
      static_cast<std::uint64_t>(message.width) * message.height;
    if (point_count == 0 || point_count > options.max_input_points_per_message) {
      throw std::runtime_error("message exceeds max_input_points_per_message or is empty");
    }
    if (total_message_count == 0) {
      first_header_stamp_ns = stamp_ns;
      first_bag_stamp_ns = static_cast<std::uint64_t>(bag_message->time_stamp);
    }
    if (stamp_ns - first_header_stamp_ns > options.max_duration_ns) {
      throw std::runtime_error("point-cloud run exceeds max_duration_ns");
    }
    const std::uint64_t window_index =
      (stamp_ns - first_header_stamp_ns) / options.chunk_duration_ns;
    if (chunk.message_count > 0 && window_index != chunk.window_index) {
      flush_chunk(options, chunk);
      ++emitted_chunks;
      chunk = Chunk{};
    }
    if (chunk.message_count == 0) {
      chunk.sequence = emitted_chunks;
      if (chunk.sequence >= options.max_chunks) {
        throw std::runtime_error("point-cloud run exceeds max_chunks");
      }
      chunk.window_index = window_index;
      chunk.first_header_stamp_ns = stamp_ns;
      chunk.first_bag_stamp_ns = static_cast<std::uint64_t>(bag_message->time_stamp);
    }
    if (chunk.input_point_count + point_count > options.max_input_points_per_chunk) {
      throw std::runtime_error("point-cloud window exceeds max_input_points_per_chunk");
    }

    Cloud source;
    pcl::fromROSMsg(message, source);
    if (source.size() != point_count) {
      throw std::runtime_error("PCL conversion changed the input point count");
    }
    for (const auto & point : source) {
      require_bounded_point(point, options.max_abs_coordinate_m);
    }
    Cloud aligned;
    pcl::transformPointCloud(source, aligned, transform);
    for (const auto & point : aligned) {
      require_bounded_point(point, options.max_abs_coordinate_m);
    }
    chunk.points.insert(chunk.points.end(), aligned.begin(), aligned.end());
    chunk.last_header_stamp_ns = stamp_ns;
    chunk.last_bag_stamp_ns = static_cast<std::uint64_t>(bag_message->time_stamp);
    ++chunk.message_count;
    chunk.input_point_count += point_count;
    ++total_message_count;
    total_input_point_count += point_count;
    last_header_stamp_ns = stamp_ns;
    last_bag_stamp_ns = static_cast<std::uint64_t>(bag_message->time_stamp);
  }
  if (total_message_count == 0) {
    throw std::runtime_error("point-cloud topic has no messages");
  }
  flush_chunk(options, chunk);

  std::cout << "pcl_version=" << PCL_VERSION_PRETTY << '\n';
  std::cout << "message_count=" << total_message_count << '\n';
  std::cout << "input_point_count=" << total_input_point_count << '\n';
  std::cout << "first_header_stamp_ns=" << first_header_stamp_ns << '\n';
  std::cout << "last_header_stamp_ns=" << last_header_stamp_ns << '\n';
  std::cout << "first_bag_stamp_ns=" << first_bag_stamp_ns << '\n';
  std::cout << "last_bag_stamp_ns=" << last_bag_stamp_ns << '\n';
  return 0;
}

}  // namespace

int main(int argc, char ** argv)
{
  try {
    return process(parse_options(argc, argv));
  } catch (const std::exception & exception) {
    std::cerr << "inspection_pcd_processor: " << exception.what() << '\n';
    return 2;
  }
}
