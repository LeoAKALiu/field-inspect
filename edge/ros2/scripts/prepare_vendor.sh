#!/usr/bin/env bash
set -euo pipefail

workspace_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
livox_root="$workspace_root/src/livox_ros_driver2"

if [[ ! -f "$livox_root/package_ROS2.xml" || ! -d "$livox_root/launch_ROS2" ]]; then
  echo "Livox source is missing. Run: vcs import src < third_party.repos" >&2
  exit 1
fi

cp "$livox_root/package_ROS2.xml" "$livox_root/package.xml"
mkdir -p "$livox_root/launch"
cp -R "$livox_root/launch_ROS2/." "$livox_root/launch/"

echo "Prepared livox_ros_driver2 for ROS 2 Humble."
