#!/usr/bin/env bash
# Static, no-route endurance recording for the powered SCOUT sensor stack.
set -euo pipefail

duration_seconds=1800
evidence_root=/data/scout_runs/_endurance_evidence

while [[ $# -gt 0 ]]; do
  case "$1" in
    --duration-seconds)
      duration_seconds="$2"
      shift 2
      ;;
    --evidence-root)
      evidence_root="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if ! [[ "$duration_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "--duration-seconds must be a positive integer" >&2
  exit 2
fi
if [[ "$evidence_root" != /* ]]; then
  echo "--evidence-root must be absolute" >&2
  exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
workspace_root="$(cd -- "$script_dir/.." && pwd)"
if [[ ! -f "$workspace_root/install/setup.bash" ]]; then
  echo "workspace is not built: $workspace_root/install/setup.bash" >&2
  exit 1
fi

set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source "$workspace_root/install/setup.bash"
set -u

system_config="$workspace_root/install/scout_bringup/share/scout_bringup/config/system.yaml"
read -r sensor_interface host_ip lidar_ip camera_ip < <(
  python3 -c '
import sys, yaml
data = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
params = data["scout_bringup"]["ros__parameters"]
print(params["sensor_interface"], params["host_ip"], params["lidar_ip"], params["camera_ip"])
' "$system_config"
)
if ! ip -brief address show "$sensor_interface" | grep -qw "$host_ip/24"; then
  echo "$sensor_interface is missing $host_ip/24; activate its sensor connection first" >&2
  exit 1
fi
for sensor_ip in "$lidar_ip" "$camera_ip"; do
  if ! ping -c 1 -W 1 "$sensor_ip" >/dev/null; then
    echo "sensor is unreachable before endurance run: $sensor_ip" >&2
    exit 1
  fi
done

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
evidence_dir="$evidence_root/$stamp"
mkdir -p -- "$evidence_dir"

if timeout 5s ros2 node list 2>/dev/null | grep -qx '/inspection_manager'; then
  echo "an inspection_manager is already running; refusing to disturb it" >&2
  exit 1
fi

launch_pid=""
recording_started=false
run_dir=""
declare -a monitor_pids=()

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ "$recording_started" == true ]]; then
    timeout 45s ros2 service call /inspection/stop std_srvs/srv/Trigger '{}' \
      >>"$evidence_dir/inspection-stop.log" 2>&1 || true
  fi
  for pid in "${monitor_pids[@]}"; do
    kill -INT "$pid" 2>/dev/null || true
  done
  # Let launch forward SIGINT once; retain group cleanup for surviving children.
  if [[ -n "$launch_pid" ]]; then
    kill -INT "$launch_pid" 2>/dev/null || kill -INT -- "-$launch_pid" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 -- "-$launch_pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 -- "-$launch_pid" 2>/dev/null; then
      kill -TERM -- "-$launch_pid" 2>/dev/null || true
      for _ in $(seq 1 5); do
        kill -0 -- "-$launch_pid" 2>/dev/null || break
        sleep 1
      done
    fi
    if kill -0 -- "-$launch_pid" 2>/dev/null; then
      kill -KILL -- "-$launch_pid" 2>/dev/null || true
    fi
    wait "$launch_pid" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

# Reject the known idle-channel split defect before launching any vehicle nodes.
if ! python3 "$workspace_root/scripts/check_mcap_writer_overlay.py" \
    "$evidence_dir/mcap-writer-preflight" \
    >"$evidence_dir/mcap-writer-preflight.log" 2>&1; then
  echo "MCAP writer preflight failed; see $evidence_dir/mcap-writer-preflight.log" >&2
  exit 1
fi

setsid ros2 launch scout_bringup demo.launch.py \
  start_camera:=true \
  start_scout:=true \
  start_livox:=true \
  start_fast_lio:=true \
  start_safety:=true \
  start_inspection:=true \
  start_collision:=true \
  start_teleop:=false \
  start_route:=false \
  >"$evidence_dir/launch.log" 2>&1 &
launch_pid=$!

service_ready=false
for _ in $(seq 1 90); do
  if ! kill -0 "$launch_pid" 2>/dev/null; then
    echo "launch exited before inspection service became ready" >&2
    exit 1
  fi
  if ros2 service type /inspection/start 2>/dev/null | \
      grep -qx 'std_srvs/srv/Trigger'; then
    service_ready=true
    break
  fi
  sleep 1
done
if [[ "$service_ready" != true ]]; then
  echo "inspection service was not ready within 90 seconds" >&2
  exit 1
fi

start_response="$(
  timeout 45s ros2 service call /inspection/start std_srvs/srv/Trigger '{}'
)"
printf '%s\n' "$start_response" >"$evidence_dir/inspection-start.log"
if [[ "$start_response" != *"success=True"* ]]; then
  echo "inspection start failed; see $evidence_dir/inspection-start.log" >&2
  exit 1
fi
recording_started=true
run_dir="$(printf '%s\n' "$start_response" | grep -o '/data/scout_runs/[^[:space:]'"'"']*' | tail -n 1)"
if [[ -z "$run_dir" ]]; then
  echo "could not parse run directory from inspection response" >&2
  exit 1
fi
printf '%s\n' "$run_dir" >"$evidence_dir/run-dir.txt"

# CameraInfo is emitted with each captured image. Avoid a duplicate 40 MB/s Python
# image subscriber on the Jetson; the final MCAP scan verifies actual image data.
for topic in /livox/lidar /livox/imu /camera/camera_info /scout_status /Odometry; do
  safe_name="${topic//\//_}"
  timeout --signal=INT "${duration_seconds}s" \
    ros2 topic hz --window 200 "$topic" \
    >"$evidence_dir/hz${safe_name}.log" 2>&1 &
  monitor_pids+=("$!")
done

if command -v tegrastats >/dev/null 2>&1; then
  timeout --signal=INT "${duration_seconds}s" tegrastats --interval 10000 \
    >"$evidence_dir/tegrastats.log" 2>&1 &
  monitor_pids+=("$!")
fi

printf 'utc,free_bytes,mem_available_kib,load_1m,max_temp_millic\n' \
  >"$evidence_dir/resources.csv"
started_epoch="$(date +%s)"
while true; do
  now_epoch="$(date +%s)"
  elapsed=$((now_epoch - started_epoch))
  if ((elapsed >= duration_seconds)); then
    break
  fi
  if ! kill -0 "$launch_pid" 2>/dev/null; then
    echo "launch exited during endurance run" >&2
    exit 1
  fi
  free_bytes="$(df --output=avail -B1 /data | tail -n 1 | tr -d ' ')"
  mem_available="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
  load_1m="$(cut -d' ' -f1 /proc/loadavg)"
  max_temp="$(awk 'BEGIN {max=0} {if ($1 > max) max=$1} END {print max}' \
    /sys/class/thermal/thermal_zone*/temp 2>/dev/null || printf '0')"
  printf '%s,%s,%s,%s,%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$free_bytes" "$mem_available" "$load_1m" "$max_temp" \
    >>"$evidence_dir/resources.csv"
  remaining=$((duration_seconds - elapsed))
  if ((remaining < 10)); then
    sleep "$remaining"
  else
    sleep 10
  fi
done

stop_response="$(
  timeout 45s ros2 service call /inspection/stop std_srvs/srv/Trigger '{}'
)"
printf '%s\n' "$stop_response" >"$evidence_dir/inspection-stop.log"
if [[ "$stop_response" != *"success=True"* ]]; then
  echo "inspection stop failed; see $evidence_dir/inspection-stop.log" >&2
  exit 1
fi
recording_started=false

ros2 run inspection_pipeline inspection_run_validate "$run_dir" \
  --recording-only \
  --output "$evidence_dir/run-validation.json" \
  >"$evidence_dir/run-validation.log" 2>&1

printf 'run_dir=%s\nevidence_dir=%s\n' "$run_dir" "$evidence_dir"
