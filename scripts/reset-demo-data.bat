@echo off
chcp 65001 >nul
REM 地下工程巡检车数字孪生原型 — 演示数据重置（Windows）
REM 仅做路径定位与调用；全部删除/重建逻辑在 services\api\app\admin.py
setlocal
cd /d "%~dp0..\services\api"

where uv >nul 2>nul || (echo 错误：未找到 uv，请参考 docs\INSTALL.md 安装 & pause & exit /b 1)
call uv run python -m app.admin reset-demo %* || (pause & exit /b 1)
endlocal
