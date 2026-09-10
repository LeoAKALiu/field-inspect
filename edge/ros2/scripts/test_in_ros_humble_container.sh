#!/usr/bin/env bash
# Run first-party ROS 2 tests inside the offline Humble test container on this Mac.
#
# One documented entry point:
#   ./scripts/test_in_ros_humble_container.sh            # all offline stages
#   ./scripts/test_in_ros_humble_container.sh <stage>    # python-independent|build|test|smoke-launch
#
# Scope boundary (do not relax): results produced here are OFFLINE CONTAINER EVIDENCE
# for algorithm, node, launch, rosbag/MCAP, TF and point-cloud logic only. They do NOT
# validate Jetson, SocketCAN/Scout base control, Hik/Livox drivers, real DDS networking,
# CUDA/Tegra performance, or any physical motion/safety behaviour.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_NAME="${SCOUT_TEST_IMAGE:-scout-mini:humble-test-arm64}"
STAGE="${1:-all}"

log() { printf '\n\033[1;34m[scout-container-test]\033[0m %s\n' "$*"; }

require_docker() {
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is not reachable." >&2
    echo "Start Docker Desktop first (open -a Docker), then re-run this script." >&2
    exit 1
  fi
  local engine_arch
  engine_arch=$(docker info --format '{{.Architecture}}')
  if [ "$engine_arch" != "aarch64" ] && [ "$engine_arch" != "arm64" ]; then
    echo "ERROR: Docker engine reports arch '$engine_arch', expected aarch64/arm64." >&2
    exit 1
  fi
}

build_image() {
  log "Building $IMAGE_NAME (linux/arm64, ros:humble-ros-base-jammy)"
  docker build --platform linux/arm64 \
    -f "$WS_ROOT/docker/ros-humble-test.Dockerfile" \
    -t "$IMAGE_NAME" "$WS_ROOT"
}

# build/, install/ and log/ live in named Docker volumes so the Mac repository tree
# stays byte-clean and incremental rebuilds are cached between runs. The staged
# runner is bind-mounted so script edits take effect without an image rebuild.
run_in_container() {
  local stage=$1
  docker run --rm --platform linux/arm64 \
    --name scout-mini-humble-test \
    -v "$WS_ROOT":/ws \
    -v "$WS_ROOT/docker/run_staged_tests.sh":/opt/scout/run_staged_tests.sh \
    -v scout_humble_build:/ws/build \
    -v scout_humble_install:/ws/install \
    -v scout_humble_log:/ws/log \
    -e ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-88}" \
    -w /ws \
    "$IMAGE_NAME" \
    bash /opt/scout/run_staged_tests.sh "$stage"
}

require_docker

case "$STAGE" in
  python-independent|build|test|smoke-launch)
    run_in_container "$STAGE"
    ;;
  all)
    # Rebuild the image only when told to (SCOUT_TEST_REBUILD=1) to keep the common
    # path fast; the image is content-pinned by the Dockerfile.
    if [ "${SCOUT_TEST_REBUILD:-0}" = "1" ] || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
      build_image
    fi
    run_in_container "all"
    ;;
  image)
    build_image
    ;;
  *)
    echo "usage: $0 [python-independent|build|test|smoke-launch|all|image]" >&2
    exit 2
    ;;
esac
