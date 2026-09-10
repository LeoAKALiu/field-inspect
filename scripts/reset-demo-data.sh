#!/usr/bin/env bash
# 地下工程巡检车数字孪生原型 — 演示数据重置（macOS/Linux）
# 仅做路径定位与调用；全部删除/重建逻辑在 services/api/app/admin.py
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/services/api"

if command -v uv >/dev/null 2>&1; then
  exec uv run python -m app.admin reset-demo "$@"
else
  exec python3 -m app.admin reset-demo "$@"
fi
