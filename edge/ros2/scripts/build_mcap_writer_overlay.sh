#!/usr/bin/env bash
# Build an isolated MCAP 0.8.0 summary fix; never install into /opt/ros.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || "$1" != /* ]]; then
  echo "usage: $0 ABSOLUTE_NEW_BUILD_DIR [VERIFIED_SOURCE_ARCHIVE]" >&2
  exit 2
fi
build_dir="$1"
if [[ -e "$build_dir" ]]; then
  echo "refusing to overwrite existing build directory: $build_dir" >&2
  exit 1
fi
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
patch_file="$script_dir/../patches/mcap-0.8.0-summary.patch"
mkdir -p "$build_dir/source" "$build_dir/lib"
archive="$build_dir/upstream-v0.8.0.tar.gz"
if [[ $# == 2 ]]; then
  cp -- "$2" "$archive"
else
  curl --fail --location --max-time 120 \
    'https://github.com/foxglove/mcap/archive/refs/tags/releases/cpp/v0.8.0.tar.gz' \
    --output "$archive"
fi
printf '%s  %s\n' \
  2833f72344308ea58639f3b363a0cf17669580ae7ab435f43f3b104cff6ef548 "$archive" \
  | sha256sum --check --status
tar -xzf "$archive" -C "$build_dir/source" --strip-components=1

# Refuse a different installed ABI rather than silently replacing its library.
for header in "$build_dir/source/cpp/mcap/include/mcap/"*.hpp; do
  cmp -- "$header" "/opt/ros/humble/include/mcap_vendor/mcap/$(basename "$header")"
done
patch --batch --forward -d "$build_dir/source" -p1 < "$patch_file"
printf '#define MCAP_IMPLEMENTATION\n#include "mcap/mcap.hpp"\n' > "$build_dir/main.cpp"
g++ -std=c++17 -O2 -fPIC -shared -Wl,-soname,libmcap.so \
  -I"$build_dir/source/cpp/mcap/include" "$build_dir/main.cpp" \
  -lzstd -llz4 -o "$build_dir/lib/libmcap.so"
sha256sum "$archive" "$patch_file" "$build_dir/lib/libmcap.so" > "$build_dir/build.sha256"
printf 'Built isolated library: %s\n' "$build_dir/lib/libmcap.so"
printf 'Use LD_LIBRARY_PATH=%s/lib:$LD_LIBRARY_PATH only for the intended test command.\n' "$build_dir"
