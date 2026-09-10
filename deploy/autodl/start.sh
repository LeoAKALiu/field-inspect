#!/usr/bin/env bash
set -euo pipefail

deploy_root="${DIGITAL_TWIN_DEPLOY_ROOT:-/root/autodl-tmp/digital-twin}"
runtime_dir="$deploy_root/runtime"
venv_dir="$deploy_root/venv"
current_link="$deploy_root/current"
nginx_bin="${NGINX_BIN:-/usr/sbin/nginx}"

release_dir="$(readlink -f "$current_link")"
if [[ -z "$release_dir" || ! -d "$release_dir/services/api/app" || ! -f "$release_dir/web/index.html" ]]; then
    echo "Invalid release target: $current_link" >&2
    exit 1
fi
if [[ ! -x "$venv_dir/bin/uvicorn" ]]; then
    echo "Missing runtime: $venv_dir/bin/uvicorn" >&2
    exit 1
fi

mkdir -p "$runtime_dir" "$release_dir/state"

stop_managed_api() {
    local pid_file="$runtime_dir/api.pid"
    [[ -f "$pid_file" ]] || return 0
    local api_pid
    api_pid="$(tr -dc '0-9' < "$pid_file")"
    [[ -n "$api_pid" ]] || return 0
    if kill -0 "$api_pid" 2>/dev/null; then
        local api_cwd
        api_cwd="$(readlink -f "/proc/$api_pid/cwd" 2>/dev/null || true)"
        if [[ "$api_cwd" != "$deploy_root"/releases/*/services/api ]]; then
            echo "Refusing to stop unmanaged API PID $api_pid (cwd=$api_cwd)" >&2
            exit 1
        fi
        kill "$api_pid"
        for _ in {1..50}; do
            kill -0 "$api_pid" 2>/dev/null || break
            sleep 0.1
        done
    fi
    rm -f "$pid_file"
}

stop_managed_nginx() {
    local pid_file="$runtime_dir/nginx.pid"
    [[ -f "$pid_file" ]] || return 0
    local nginx_pid
    nginx_pid="$(tr -dc '0-9' < "$pid_file")"
    [[ -n "$nginx_pid" ]] || return 0
    if kill -0 "$nginx_pid" 2>/dev/null; then
        local nginx_cmd
        nginx_cmd="$(tr '\0' ' ' < "/proc/$nginx_pid/cmdline" 2>/dev/null || true)"
        if [[ "$nginx_cmd" != *"$runtime_dir/nginx.conf"* ]]; then
            echo "Refusing to stop unmanaged Nginx PID $nginx_pid" >&2
            exit 1
        fi
        "$nginx_bin" -p "$runtime_dir/" -c "$runtime_dir/nginx.conf" -s quit
        for _ in {1..50}; do
            kill -0 "$nginx_pid" 2>/dev/null || break
            sleep 0.1
        done
    fi
    rm -f "$pid_file"
}

stop_managed_nginx
stop_managed_api

sed \
    -e "s|@@RUNTIME_DIR@@|$runtime_dir|g" \
    -e "s|@@WEB_ROOT@@|$release_dir/web|g" \
    "$deploy_root/deploy/nginx.conf.template" > "$runtime_dir/nginx.conf"

(
    cd "$release_dir/services/api"
    export PYTHONPATH="$release_dir/services/api"
    export TWIN_DB_PATH="$release_dir/state/twin.db"
    nohup "$venv_dir/bin/uvicorn" app.main:app \
        --host 127.0.0.1 --port 8000 \
        </dev/null >"$runtime_dir/api.log" 2>&1 &
    echo "$!" > "$runtime_dir/api.pid"
)

for _ in {1..100}; do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null

"$nginx_bin" -t -p "$runtime_dir/" -c "$runtime_dir/nginx.conf"
"$nginx_bin" -p "$runtime_dir/" -c "$runtime_dir/nginx.conf"

for _ in {1..100}; do
    if curl -fsS http://127.0.0.1:6006/api/health >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done
curl -fsS http://127.0.0.1:6006/api/health
echo
echo "Digital Twin is serving release $(basename "$release_dir") on port 6006."
