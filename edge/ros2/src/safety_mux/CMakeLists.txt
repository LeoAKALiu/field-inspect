cmake_minimum_required(VERSION 3.8)
project(safety_mux)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_msgs REQUIRED)

add_library(${PROJECT_NAME}_core src/arbitration_core.cpp)
target_include_directories(${PROJECT_NAME}_core PUBLIC include)
target_compile_features(${PROJECT_NAME}_core PUBLIC cxx_std_17)

add_executable(safety_mux_node src/safety_mux_node.cpp)
target_compile_features(safety_mux_node PUBLIC cxx_std_17)
target_link_libraries(safety_mux_node ${PROJECT_NAME}_core)
ament_target_dependencies(safety_mux_node diagnostic_msgs geometry_msgs rclcpp sensor_msgs std_msgs)

add_library(${PROJECT_NAME}_collision_core src/collision_core.cpp)
target_include_directories(${PROJECT_NAME}_collision_core PUBLIC include)
target_compile_features(${PROJECT_NAME}_collision_core PUBLIC cxx_std_17)

add_executable(collision_monitor_node src/collision_monitor_node.cpp)
target_compile_features(collision_monitor_node PUBLIC cxx_std_17)
target_link_libraries(collision_monitor_node ${PROJECT_NAME}_collision_core)
ament_target_dependencies(collision_monitor_node diagnostic_msgs rclcpp sensor_msgs std_msgs)

install(
  TARGETS ${PROJECT_NAME}_core ${PROJECT_NAME}_collision_core safety_mux_node collision_monitor_node
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION lib/${PROJECT_NAME}
)
install(DIRECTORY include/ DESTINATION include)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  find_package(ament_lint_auto REQUIRED)
  ament_add_gtest(test_arbitration_core test/test_arbitration_core.cpp)
  if(TARGET test_arbitration_core)
    target_link_libraries(test_arbitration_core ${PROJECT_NAME}_core)
  endif()
  ament_add_gtest(test_collision_core test/test_collision_core.cpp)
  if(TARGET test_collision_core)
    target_link_libraries(test_collision_core ${PROJECT_NAME}_collision_core)
  endif()
  ament_lint_auto_find_test_dependencies()
endif()

ament_export_include_directories(include)
ament_export_libraries(${PROJECT_NAME}_core ${PROJECT_NAME}_collision_core)
ament_package()
