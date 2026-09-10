# 构建和运行

平台使用 Node 20+、pnpm 9.15.0、Python 3.11+、uv。根目录执行 `pnpm install --frozen-lockfile`；services/api 中执行 `uv sync --frozen --no-dev`。根目录 `pnpm build` 编译契约、查看器、网页和 Python API，当前脚本没有调用测试。`pnpm check` 包含测试，本阶段不得执行。

`pnpm dev` 启动平台，API 默认 127.0.0.1:8000。TWIN_DB_PATH 和 TWIN_IMPORT_ROOT 指向私有持久目录。

车端在 edge/ros2，目标 Ubuntu 22.04 / ROS Humble / Jetson ARM64。保留 ROS 包名。ProArt Windows x86 与 Ubuntu 24.04 WSL 不能冒充部署目标；不得把 x86 构建产物部署到 ARM64。

本阶段保留测试文件，全部测试未执行。编译不是测试通过。
