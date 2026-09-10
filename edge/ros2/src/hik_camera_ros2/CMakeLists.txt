cmake_minimum_required(VERSION 3.8)
project(hik_camera_ros2)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(camera_info_manager REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)

set(HIK_MVS_ROOT "$ENV{HIK_MVS_ROOT}" CACHE PATH "Hikrobot MVS SDK root")
find_path(HIK_MVS_INCLUDE NAMES MvCameraControl.h
  PATHS "${HIK_MVS_ROOT}/include" /opt/MVS/include)
find_library(HIK_MVS_LIBRARY NAMES MvCameraControl
  PATHS "${HIK_MVS_ROOT}/lib/aarch64" "${HIK_MVS_ROOT}/lib64" /opt/MVS/lib/aarch64 /opt/MVS/lib64)

find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
  pkg_check_modules(ARAVIS QUIET aravis-0.8)
endif()

add_library(${PROJECT_NAME}_backend src/camera_backend.cpp)
target_include_directories(${PROJECT_NAME}_backend PUBLIC include)
target_compile_features(${PROJECT_NAME}_backend PUBLIC cxx_std_17)

if(HIK_MVS_INCLUDE AND HIK_MVS_LIBRARY)
  target_compile_definitions(${PROJECT_NAME}_backend PRIVATE USE_HIK_MVS)
  target_include_directories(${PROJECT_NAME}_backend PRIVATE "${HIK_MVS_INCLUDE}")
  target_link_libraries(${PROJECT_NAME}_backend PRIVATE "${HIK_MVS_LIBRARY}")
endif()

if(ARAVIS_FOUND)
  target_compile_definitions(${PROJECT_NAME}_backend PRIVATE USE_ARAVIS)
  target_include_directories(${PROJECT_NAME}_backend PRIVATE ${ARAVIS_INCLUDE_DIRS})
  target_link_libraries(${PROJECT_NAME}_backend PRIVATE ${ARAVIS_LIBRARIES})
endif()

add_executable(hik_camera_node src/hik_camera_node.cpp)
target_compile_features(hik_camera_node PUBLIC cxx_std_17)
target_link_libraries(hik_camera_node ${PROJECT_NAME}_backend)
ament_target_dependencies(hik_camera_node camera_info_manager diagnostic_msgs rclcpp sensor_msgs)

install(TARGETS hik_camera_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()

  ament_add_gtest(test_camera_info_validation test/test_camera_info_validation.cpp)
  target_include_directories(test_camera_info_validation PRIVATE include)
  ament_target_dependencies(test_camera_info_validation sensor_msgs)
endif()

ament_export_include_directories(include)
ament_export_libraries(${PROJECT_NAME}_backend)
ament_package()
