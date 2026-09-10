#!/usr/bin/env bash
# Staged first-party test runner. Executed INSIDE the scout-mini ROS 2 Humble test
# container (see scripts/test_in_ros_humble_container.sh); workspace is mounted at /ws.
#
# Offline evidence only: passing here does not validate Jetson, SocketCAN, the Scout
# base, Hik/Livox drivers, DDS networking to real devices, CUDA/Tegra performance or
# vehicle safety.
set -uo pipefail

STAGE="${1:-all}"
WORKSPACE=/ws
cd "$WORKSPACE"

# Source the ROS underlay on a clean overlay chain (repo convention: the only
# permitted parent prefix is /opt/ros/humble).
set +u
source /opt/ros/humble/setup.bash
set -u

PASS=()
FAIL=()
EXCLUDED=()

record_pass() { PASS+=("$1"); }
record_fail() { FAIL+=("$1"); }
record_excluded() { EXCLUDED+=("$1"); }

run_stage_python_independent() {
  echo "=== stage: python-independent (no ROS imports required) ==="
  local compile_targets=(src/inspection_pipeline src/scout_bringup)
  if python3 -m compileall -q "${compile_targets[@]}"; then
    record_pass "python compileall (inspection_pipeline, scout_bringup)"
  else
    record_fail "python compileall (inspection_pipeline, scout_bringup)"
    return 1
  fi

  # ROS-free modules are derived, not hard-coded: a module counts as ROS-free when
  # it does not textually reference rclpy/rosbag2/launch/ament_index. pytest must run
  # from the package root so `from inspection_pipeline...` resolves without an install.
  local ros_free_modules=()
  while IFS= read -r f; do
    ros_free_modules+=("test/$(basename "$f")")
  done < <(grep -LE "rclpy|rosbag2_py|launch|ament_index" src/inspection_pipeline/test/test_*.py)

  if (cd src/inspection_pipeline && python3 -m pytest -q "${ros_free_modules[@]}"); then
    record_pass "inspection_pipeline ROS-free pytest modules (${#ros_free_modules[@]} modules)"
  else
    record_fail "inspection_pipeline ROS-free pytest modules (${#ros_free_modules[@]} modules)"
    return 1
  fi
}

run_stage_build() {
  echo "=== stage: build (colcon, first-party packages) ==="
  if colcon build --symlink-install --packages-up-to scout_bringup; then
    record_pass "colcon build --packages-up-to scout_bringup (all five first-party packages)"
  else
    record_fail "colcon build --packages-up-to scout_bringup (all five first-party packages)"
    return 1
  fi
}

run_stage_test() {
  echo "=== stage: test (colcon test + test-result) ==="
  set +u
  source "$WORKSPACE/install/setup.bash"
  set -u

  local full_test_packages=(safety_mux hik_camera_ros2 inspection_pointcloud_tools inspection_pipeline)
  if colcon test --packages-select "${full_test_packages[@]}"; then
    record_pass "colcon test ${full_test_packages[*]}"
  else
    record_fail "colcon test ${full_test_packages[*]}"
  fi

  if colcon test-result --verbose; then
    record_pass "colcon test-result (0 failures)"
  else
    record_fail "colcon test-result (failures above)"
  fi

  # scout_bringup: `scout_bringup/health_monitor.py` imports `scout_msgs` (vendor
  # messages from third_party/scout_ros2) at module scope. The Mac container
  # intentionally does not import vendor source, so:
  # - three test modules cannot even collect (they import health_monitor or scout_msgs);
  # - test_collision_pipeline launches demo.launch.py, whose always-on health_monitor
  #   node crashes without scout_msgs, so the stub chassis heartbeat never appears.
  # These four modules run on Jetson instead.
  record_excluded "scout_bringup/test/test_health_monitor_pipeline.py (imports vendor scout_msgs; Jetson-only coverage)"
  record_excluded "scout_bringup/test/test_health_monitor_logic.py (imports scout_bringup.health_monitor -> vendor scout_msgs; Jetson-only coverage)"
  record_excluded "scout_bringup/test/test_health_monitor_tf.py (imports scout_bringup.health_monitor -> vendor scout_msgs; Jetson-only coverage)"
  record_excluded "scout_bringup/test/test_collision_pipeline.py (launches health_monitor node which needs vendor scout_msgs at runtime; Jetson-only coverage)"

  echo "--- scout_bringup pytest (vendor-dependent modules excluded) ---"
  if python3 -m pytest -q \
      --ignore=src/scout_bringup/test/test_health_monitor_pipeline.py \
      --ignore=src/scout_bringup/test/test_health_monitor_logic.py \
      --ignore=src/scout_bringup/test/test_health_monitor_tf.py \
      --ignore=src/scout_bringup/test/test_collision_pipeline.py \
      src/scout_bringup/test; then
    record_pass "scout_bringup pytest (10 of 14 modules, vendor-dependent modules excluded)"
  else
    record_fail "scout_bringup pytest (10 of 14 modules, vendor-dependent modules excluded)"
  fi
}

run_stage_smoke_launch() {
  echo "=== stage: smoke-launch (software-only demo, no devices) ==="
  set +u
  source "$WORKSPACE/install/setup.bash"
  set -u

  local log_file=/tmp/scout_smoke_launch.log
  # SIGINT after the soak window; ros2 launch shuts down cleanly on INT.
  timeout --signal=INT 25 \
    ros2 launch scout_bringup demo.launch.py \
      start_camera:=false start_scout:=false start_livox:=false start_fast_lio:=false \
      >"$log_file" 2>&1
  local rc=$?
  if [ $rc -ne 0 ] && [ $rc -ne 124 ]; then
    echo "smoke launch exited with rc=$rc; output:"; tail -40 "$log_file"
    record_fail "smoke-launch demo.launch.py (all devices off)"
    return 1
  fi
  if grep -E "PREFLIGHT|process has died|Shutting down.*error" "$log_file" >/dev/null; then
    echo "smoke launch log contains failure markers:"; grep -E "PREFLIGHT|process has died" "$log_file" | head -10
    record_fail "smoke-launch demo.launch.py (all devices off)"
    return 1
  fi
  record_pass "smoke-launch demo.launch.py, all devices off, ${rc} (0=clean SIGINT, 124=timeout kill)"
}

summary() {
  echo
  echo "==================== container test summary ===================="
  echo "PASSED:"
  local item
  for item in "${PASS[@]:-}"; do [ -n "$item" ] && echo "  [PASS] $item"; done
  echo "INTENTIONALLY EXCLUDED (vendor/hardware boundary, offline evidence only):"
  for item in "${EXCLUDED[@]:-}"; do [ -n "$item" ] && echo "  [SKIP] $item"; done
  if [ "${#FAIL[@]}" -gt 0 ]; then
    echo "FAILURES:"
    for item in "${FAIL[@]}"; do echo "  [FAIL] $item"; done
    echo "RESULT: FAILURE"
    return 1
  fi
  echo "RESULT: SUCCESS (offline container evidence; not Jetson/sensor/CAN/vehicle acceptance)"
  return 0
}

# Stages run in fixed order with build before anything that needs install/.
case "$STAGE" in
  python-independent)
    run_stage_python_independent || true
    summary
    ;;
  build)
    run_stage_build || true
    summary
    ;;
  test)
    run_stage_test || true
    summary
    ;;
  smoke-launch)
    run_stage_smoke_launch || true
    summary
    ;;
  all)
    run_stage_python_independent || true
    run_stage_build || true
    run_stage_test || true
    summary
    ;;
  *)
    echo "unknown stage: $STAGE (expected: python-independent|build|test|smoke-launch|all)" >&2
    exit 2
    ;;
esac
