#!/usr/bin/env bash
set -u

failed=0
warned=0

source_ros_setup() {
  local setup_file=$1
  set +u
  # ROS-generated setup chains read optional variables that are not always set.
  # shellcheck disable=SC1090
  source "$setup_file"
  set -u
}

workspace_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$workspace_root"

check_command() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "OK   command: $1"
  else
    echo "MISS command: $1"
    failed=1
  fi
}

check_command ros2
check_command colcon
check_command vcs
check_command ip
check_command mcap
check_command findmnt
check_command lsblk
check_command sync
check_command udisksctl

if [[ -f /opt/ros/humble/setup.bash ]]; then
  source_ros_setup /opt/ros/humble/setup.bash
fi

if [[ -f "$workspace_root/install/setup.bash" ]]; then
  source_ros_setup "$workspace_root/install/setup.bash"
fi

if ros2 pkg prefix rosbag2_storage_mcap >/dev/null 2>&1; then
  echo "OK   rosbag storage: mcap"
else
  echo "MISS rosbag storage: mcap (run ./scripts/install_deps.sh)"
  failed=1
fi

if command -v mcap >/dev/null 2>&1 && mcap --version | grep -q 'mcap 0\.3\.0'; then
  echo "OK   MCAP validator: mcap-cli 0.3.0"
else
  echo "MISS MCAP validator: pinned v0.3.0 (run ./scripts/install_mcap_cli.sh)"
  failed=1
fi

if python3 - <<'PY'
import shapely

parts = tuple(int(item) for item in shapely.__version__.split(".")[:2])
if parts != (1, 8):
    raise RuntimeError(
        f"unsupported Shapely {shapely.__version__}; expected Jammy 1.8.x"
    )
print(f"OK   route dependency: python3-shapely {shapely.__version__}")
PY
then
  :
else
  echo "MISS route dependency: python3-shapely (run ./scripts/install_deps.sh)"
  failed=1
fi

for nav2_package in nav2_controller nav2_lifecycle_manager nav2_msgs \
  nav2_regulated_pure_pursuit_controller; do
  if ros2 pkg prefix "$nav2_package" >/dev/null 2>&1; then
    echo "OK   route dependency: $nav2_package"
  else
    echo "MISS route dependency: $nav2_package (run ./scripts/install_deps.sh)"
    failed=1
  fi
done

validate_config() {
  # NOTE: use type -P / command to bypass this shell function itself;
  # plain `command -v` also matches functions, which would recurse.
  if type -P validate_config >/dev/null 2>&1; then
    if command validate_config "$workspace_root/src/scout_bringup/config/system.yaml"; then
      return 0
    fi
    failed=1
    return 1
  fi

  if python3 - <<'PY' "$workspace_root/src/scout_bringup/config/system.yaml"; then
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src/scout_bringup")))
from scout_bringup.config_validator import validate_config_file

errors = validate_config_file(Path(sys.argv[1]))
for item in errors:
    print(f"ERROR {item}", file=sys.stderr)
raise SystemExit(1 if errors else 0)
PY
    echo "OK   config: src/scout_bringup/config/system.yaml"
  else
    failed=1
  fi
}

validate_config

if [[ ! -d "$workspace_root/src/livox_ros_driver2" ]]; then
  echo "WARN vendor: src/livox_ros_driver2 missing (run ./scripts/install_deps.sh)"
  warned=1
fi

if [[ ! -d "$workspace_root/src/third_party/scout_ros2" ]]; then
  echo "WARN vendor: src/third_party/scout_ros2 missing (run ./scripts/install_deps.sh)"
  warned=1
fi

echo "INFO network interfaces"
ip -br addr 2>/dev/null || true

echo "INFO routes"
ip -br route 2>/dev/null || ip route 2>/dev/null || true

if command -v preflight_check >/dev/null 2>&1; then
  preflight_check "$workspace_root/src/scout_bringup/config/system.yaml" \
    --workspace-root "$workspace_root" \
    --start-camera true \
    --start-scout false \
    --start-livox false \
    --start-fast-lio false \
    --start-inspection true || warned=1
elif python3 - <<'PY' "$workspace_root/src/scout_bringup/config/system.yaml" "$workspace_root"; then
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src/scout_bringup")))
from scout_bringup.config_validator import load_config
from scout_bringup.preflight import collect_preflight_diagnostics

config = load_config(Path(sys.argv[1]))
workspace_root = Path(sys.argv[2])
for item in collect_preflight_diagnostics(
    config,
    start_camera=True,
    start_scout=False,
    start_livox=False,
    start_fast_lio=False,
    start_inspection=True,
    start_route=False,
    workspace_root=workspace_root,
):
    print(item.format())
PY
  echo "OK   preflight checks completed"
else
  warned=1
fi

if [[ "$failed" -ne 0 ]]; then
  exit "$failed"
fi

if [[ "$warned" -ne 0 ]]; then
  echo "WARN doctor finished with warnings; see messages above"
  exit 2
fi

echo "OK   doctor checks passed"
