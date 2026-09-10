#!/usr/bin/env bash
set -euo pipefail

workspace_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$workspace_root"

source_ros_setup() {
  local setup_file=$1
  set +u
  # ROS-generated setup chains read optional variables that are not always set.
  # shellcheck disable=SC1090
  source "$setup_file"
  set -u
}

if [[ ! -f /opt/ros/humble/setup.bash ]]; then
  echo "ERROR ROS 2 Humble not found at /opt/ros/humble/setup.bash" >&2
  exit 1
fi

source_ros_setup /opt/ros/humble/setup.bash

missing=0

require_command() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "OK   command: $1"
  else
    echo "MISS command: $1"
    missing=1
  fi
}

require_command vcs
require_command rosdep
require_command colcon

if ! rosdep update >/dev/null 2>&1; then
  echo "WARN rosdep update failed; continuing with cached rules" >&2
fi

echo "INFO importing third-party sources"
vcs import src < third_party.repos

echo "INFO preparing Livox vendor files"
./scripts/prepare_vendor.sh

echo "INFO installing pinned MCAP validation CLI"
./scripts/install_mcap_cli.sh

echo "INFO installing rosdep dependencies"
rosdep install --from-paths src --ignore-src -r -y

if [[ "$missing" -ne 0 ]]; then
  exit "$missing"
fi

echo "OK   dependencies installed; next: colcon build --symlink-install"
