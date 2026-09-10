#!/usr/bin/env bash
# Build against the ROS system spdlog ABI without replacing /usr/local libraries.
set -euo pipefail
if [[ $# != 2 || "$1" != /* ]]; then
  echo "usage: $0 ABSOLUTE_NEW_BUILD_DIR EXISTING_LIVOX_SDK2_REPOSITORY" >&2
  exit 2
fi
build_dir="$1"
source_repo="$2"
revision=08f523c930b2f0ba1e98a6afaa8d7476bf479908
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -e "$build_dir" ]]; then
  echo "refusing to overwrite: $build_dir" >&2
  exit 1
fi
git -C "$source_repo" cat-file -e "$revision^{commit}"
mkdir -p "$build_dir/source"
git -C "$source_repo" archive "$revision" | tar -x -C "$build_dir/source"
patch_file="$script_dir/../patches/livox-sdk2-system-spdlog.patch"
patch --batch --forward -d "$build_dir/source" -p1 < "$patch_file"
cmake -S "$build_dir/source" -B "$build_dir/build" -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build "$build_dir/build" --target livox_lidar_sdk_shared --parallel 2
library_dir="$build_dir/build/sdk_core"
if [[ "${ASTRA_RUN_BUILD_PROBES:-0}" == "1" ]]; then
g++ -std=c++11 -DSPDLOG_COMPILED_LIB -DSPDLOG_FMT_EXTERNAL   "$script_dir/test/check_livox_sdk_logging.cpp" -L"$library_dir"   -llivox_lidar_sdk_shared -lspdlog -lfmt -pthread -o "$build_dir/check_logging"
LD_LIBRARY_PATH="$library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$build_dir/check_logging"
else
  printf "Logging probe not executed (build-only mode).\n"
fi
printf '%s\n' "$revision" > "$build_dir/source-commit.txt"
sha256sum "$patch_file" "$library_dir/liblivox_lidar_sdk_shared.so" > "$build_dir/build.sha256"
printf 'Use LD_LIBRARY_PATH=%s:$LD_LIBRARY_PATH for the intended launch command.\n' "$library_dir"
