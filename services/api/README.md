# Digital Twin API

地下工程巡检车数字孪生原型后端（FastAPI + SQLite + WebSocket），离线可运行。
契约 v2 见 `packages/contracts/`（REST 前缀 `/api`，坐标系 `scene_local_yup`）。

## 运行

```bash
pip install -r services/api/requirements.txt   # Python ≥ 3.11
cd services/api
uvicorn app.main:app --reload --port 8000
```

环境变量：

- `TWIN_DB_PATH`：SQLite 路径（默认 `data/var/twin.db`，测试用临时路径）
- `TWIN_IMPORT_ROOT`：服务器本地巡检包目录（默认 `data/imports`）
- `TWIN_SIM_TICK_MS`：模拟推送周期毫秒（默认 1000）
- `TWIN_SEED`：模拟随机种子（默认 42）

启动时建表幂等，仅当库为空时从 `data/demo/` 载入种子数据。

## 端点示例

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/scenes
curl http://localhost:8000/api/scenes/scene-001/metadata
curl "http://localhost:8000/api/events?status=open"
curl -X POST http://localhost:8000/api/events/export \
  -H 'Content-Type: application/json' -d '{"format": "csv", "status": "open"}'
```

WebSocket：`/ws/vehicle?task_id=<simulation 任务>`（车辆推送）、`/ws/replay/{task_id}`（回放）。

正式巡检包为 v2（`inspection-package-v2.schema.json`）。不通过 HTTP 上传或触发导入。
先复制完整 BagIt 目录到 `$TWIN_IMPORT_ROOT/inbox/<bundle_name>/`，再在服务器本机：

```bash
uv run python -m app.cli register-scene --file <registration.json> --operator-label "<操作人标注>"
uv run python -m app.cli import-package <bundle_name>
uv run python -m app.cli accept-run <run_id> --operator-label "<操作人标注>" --first-frame pass --last-frame pass
uv run python -m app.cli withdraw-acceptance <run_id> --operator-label "<操作人标注>" --reason "<原因>"
uv run python -m app.cli verify-backup <run_id> --backup-root <path>
uv run python -m app.cli cleanup-inbox <bundle_name> --run-id <run_id>
uv run python -m app.cli serve-evidence-report --port 8090
curl http://localhost:8000/api/imports
curl http://localhost:8000/api/tasks/<run_id>/display-trajectory
```

`TWIN_DB_PATH` 与 `TWIN_IMPORT_ROOT` 必须指向代码发布目录之外的持久位置（默认 `data/var/twin.db` 与 `data/imports`）。

## 测试

```bash
python3 -m pytest services/api/tests -q
```

## 结构

- `app/main.py` — 应用工厂 `create_app()`
- `app/routers/` — REST 路由（前缀 `/api`）
- `app/ws.py` / `app/simulation.py` / `app/replay.py` — WS 网关、模拟器、回放引擎
- `app/db.py` / `app/seed.py` — SQLite 访问层与种子载入
