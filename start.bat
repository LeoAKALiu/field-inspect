@echo off
chcp 65001 >nul
REM 地下工程巡检车数字孪生原型 — 一键启动（Windows）
REM 双击运行，或命令行执行：start.bat
setlocal
cd /d "%~dp0"

echo ==^> 检查依赖环境 ...
where node >nul 2>nul || (echo 错误：未找到 Node.js，请先安装 Node.js ^>= 20 & pause & exit /b 1)
where pnpm >nul 2>nul || (echo ==^> 启用 pnpm（corepack）... & corepack enable && corepack prepare pnpm@9.15.0 --activate)
where uv >nul 2>nul || (echo 错误：未找到 uv，请参考 docs\INSTALL.md 安装 & pause & exit /b 1)

if not exist node_modules (
  echo ==^> 首次运行，安装前端依赖 ...
  call pnpm install || (pause & exit /b 1)
)
if not exist services\api\.venv (
  echo ==^> 首次运行，安装后端依赖 ...
  pushd services\api && call uv sync && popd
)
if not exist packages\contracts\dist (
  echo ==^> 构建共享契约包 ...
  call pnpm --filter @digital-twin/contracts build
)
if not exist packages\twin-viewer\dist (
  echo ==^> 构建三维引擎包 ...
  call pnpm --filter @digital-twin/twin-viewer build
)

echo ==^> 启动后端 API（http://127.0.0.1:8000）...
start "digital-twin-api" cmd /k "cd /d %~dp0services\api && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo ==^> 启动前端（http://127.0.0.1:5173）...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5173"
cd /d "%~dp0apps\web"
call pnpm dev --host 127.0.0.1
