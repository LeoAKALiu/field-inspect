#!/usr/bin/env bash
set -euo pipefail

deploy_root="${DIGITAL_TWIN_DEPLOY_ROOT:-/root/autodl-tmp/digital-twin}"
runtime_dir="$deploy_root/runtime"

echo "release=$(basename "$(readlink -f "$deploy_root/current")")"
for service_name in api nginx; do
    pid_file="$runtime_dir/$service_name.pid"
    service_pid=""
    [[ -f "$pid_file" ]] && service_pid="$(tr -dc '0-9' < "$pid_file")"
    if [[ -n "$service_pid" ]] && kill -0 "$service_pid" 2>/dev/null; then
        echo "$service_name=running pid=$service_pid"
    else
        echo "$service_name=stopped"
    fi
done

curl -fsS http://127.0.0.1:6006/api/health
echo
