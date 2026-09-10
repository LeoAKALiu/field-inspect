#!/bin/bash
# 地下工程巡检车数字孪生原型 — 一键启动（macOS）
# 双击运行，或终端执行：./start.command
set -e
cd "$(dirname "$0")"

echo "==> 检查依赖环境 ..."
if ! command -v node >/dev/null 2>&1; then
  echo "错误：未找到 Node.js，请先安装 Node.js >= 20（https://nodejs.org）"; read -r; exit 1
fi
if ! command -v pnpm >/dev/null 2>&1; then
  echo "==> 启用 pnpm（corepack）..."
  corepack enable && corepack prepare pnpm@9.15.0 --activate
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "错误：未找到 uv，请先安装（https://docs.astral.sh/uv/）或参考 docs/INSTALL.md 使用 pip 方式"; read -r; exit 1
fi

if [ ! -d node_modules ]; then
  echo "==> 首次运行，安装前端依赖 ..."
  pnpm install
fi
if [ ! -d services/api/.venv ]; then
  echo "==> 首次运行，安装后端依赖 ..."
  (cd services/api && uv sync)
fi
if [ ! -d packages/contracts/dist ]; then
  echo "==> 构建共享契约包 ..."
  pnpm --filter @digital-twin/contracts build
fi
if [ ! -d packages/twin-viewer/dist ]; then
  echo "==> 构建三维引擎包 ..."
  pnpm --filter @digital-twin/twin-viewer build
fi

echo "==> 启动后端 API（http://127.0.0.1:8000）..."
(cd services/api && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT

echo "==> 启动前端（http://127.0.0.1:5173）..."
sleep 2
open "http://127.0.0.1:5173" 2>/dev/null || true
(cd apps/web && pnpm dev --host 127.0.0.1)
