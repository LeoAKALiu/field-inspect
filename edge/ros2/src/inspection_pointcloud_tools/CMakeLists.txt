cmake_minimum_required(VERSION 3.8)
project(inspection_pointcloud_tools)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

find_package(ament_cmake REQUIRED)
find_package(Eigen3 REQUIRED)
find_package(PCL REQUIRED COMPONENTS common filters io)
find_package(pcl_conversions REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rosbag2_cpp REQUIRED)
find_package(rosbag2_storage REQUIRED)
find_package(sensor_msgs REQUIRED)

add_executable(inspection_pcd_processor src/inspection_pcd_processor.cpp)
target_include_directories(inspection_pcd_processor PRIVATE ${PCL_INCLUDE_DIRS})
target_link_libraries(
  inspection_pcd_processor
  Eigen3::Eigen
  ${PCL_LIBRARIES}
)
ament_target_dependencies(
  inspection_pcd_processor
  pcl_conversions
  rclcpp
  rosbag2_cpp
  rosbag2_storage
  sensor_msgs
)

install(TARGETS inspection_pcd_processor DESTINATION lib/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
