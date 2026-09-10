# 地下工程巡检车数字孪生原型 — 后端架构

> 状态：契约 v2 冻结（API version 0.2.0）。任何修改须经架构负责人评审。
> 适用范围：离线可运行的数字孪生原型后端（FastAPI + SQLite + WebSocket）。

## 1. 目标与边界

为地下工程（隧道/管廊）巡检车数字孪生提供后端原型，支持三种数据模式：

| 模式 | 说明 | 当前状态 |
|---|---|---|
| `simulation` | 后端内置模拟器生成车辆轨迹、检测事件、传感器曲线 | 已实现 |
| `replay` | 回放 `data/demo/` 中的历史任务数据 | 已实现 |
| `live_pending` | 甲方实时数据接入（预留） | **仅预留接口，未接入真实数据，不虚构** |

三种数据来源的取值即 `SourceType = simulation | replay | live_pending`，
不存在"甲方真实数据已接入"状态。

明确不做的事：

- 不实现用户认证/权限（原型内网离线运行）。
- 不虚构任何甲方协议字段。凡未获得甲方协议支持的字段，一律在契约中以
  `x-provenance: simulated | pending_confirmation` 标记（见 §8）。
- 不修改 `apps/web`、`packages/twin-viewer`、`assets/tunnel`（见 §6 TwinViewer 契约边界）。

## 2. 总体结构

```
apps/web  ─┐
packages/twin-viewer ─┤  (前端/三维 viewer，本仓库其他负责人所有，仅消费契约)
                      │  HTTP + WebSocket
                      ▼
services/api          FastAPI 应用
  ├─ app/routers/     REST 路由（scenes/tasks/events/devices/data-sources/export/live）
  ├─ app/ws.py        WebSocket 网关（车辆位置推送 / 任务回放推送）
  ├─ app/simulation.py 模拟器（确定性伪随机，可复现）
  ├─ app/replay.py     回放引擎（按时间轴播放 TrajectoryPoint）
  ├─ app/db.py         SQLite 访问层（stdlib sqlite3，无 ORM）
  └─ app/seed.py       启动时从 data/demo/ 载入种子数据
data/demo             种子数据（场景、任务、轨迹、事件、设备）
packages/contracts    对外契约（src/domain.ts / src/twin-viewer.ts /
                      src/inspection-package.ts / domain.schema.json /
                      inspection-package-v2.schema.json / run-bundle.schema.json /
                      openapi.yaml / websocket-events.schema.json）
```

契约先行：`packages/contracts/` 是前后端唯一事实来源（TypeScript 冻结契约为
`src/domain.ts`、`src/twin-viewer.ts` 与 `src/inspection-package.ts`）；后端实现必须与
`openapi.yaml`、`domain.schema.json`、`inspection-package-v2.schema.json`、
`websocket-events.schema.json` 保持一致，验收以 `docs/ACCEPTANCE.md` 为准。

## 3. 核心领域对象

10 个核心对象，完整字段定义见 `packages/contracts/domain.schema.json`
（与 `packages/contracts/src/domain.ts` 逐一对应）：

- **Scene**：地下工程场景（隧道段），持有几何模型引用与空间范围。
- **PatrolTask**：一次巡检任务，关联 Scene，含模式（simulation/replay/live_pending）、
  状态机 `pending → running → completed | aborted`。
- **VehicleState**：巡检车瞬时状态（位置、航向、速度、电量），由 WS 周期推送。
- **TrajectoryPoint**：轨迹点，任务的有序历史位置序列，回放的数据源。
- **DetectionEvent**：异常检测事件（裂缝/渗水/剥落/设备故障等），含严重程度与
  复核状态机 `open → acknowledged → resolved | false_positive`
  （异常候选不等于已确认灾害）。
- **SensorDevice**：固定在场景中的监测设备（离层仪 delamination、位移计 displacement、
  收敛计 convergence、应力计 stress、温度、湿度、气体）。
- **SensorReading**：传感器读数，模拟曲线为确定性正弦+噪声（按设备 id 定种子）。
- **SpatialBinding**：对象（设备/事件/车辆）到三维场景坐标的绑定关系。
- **DataSourceStatus**：三种数据模式各自的运行状态与最近更新时间。
- **SceneMetadata**：场景元数据（三维资产引用、坐标系声明与巡检路线），见 §5。

每个领域对象带必填 `source_type`（simulation|replay|live_pending）与
`provenance` 子对象。

## 4. 运行模式与数据流

### 4.1 simulation（默认）

- 内置模拟器以固定频率（默认 1 Hz，可配）沿预定义路线推进巡检车，
  生成 `VehicleState` 并通过 WS 推送给订阅者。
- 模拟器按确定性伪随机（种子固定）在途中产生 `DetectionEvent`，保证多次运行结果一致。
- `SensorReading` 不落库，按请求即时生成确定性曲线（同样的 from/to/limit 必得同样结果）。

### 4.2 replay

- 历史任务的 `TrajectoryPoint` 序列存于 SQLite（种子来自 `data/demo/`）。
- 客户端通过 `WS /ws/replay/{task_id}` 发起回放，支持
  `pause / resume / speed / seek` 控制消息；回放进度与事件按原始时间轴×倍速推送。
- 回放为只读：不改变任务与事件的历史状态。

### 4.3 live_pending（预留，不虚构）

- `POST /api/live/ingest` 已在 OpenAPI 中定义报文信封，服务端一律返回
  `501 Not Implemented`，消息明确"待甲方协议确认后启用"。
- `DataSourceStatus` 中 live_pending 源状态固定为 `reserved`，`last_update` 为 null。
- 待甲方提供协议后：补充字段映射表 → 更新契约中的 `x-provenance` 标记 → 实现接入适配器。
- 在此之前不得虚构任何"真实数据已接入"的界面状态或数据。

### 4.4 SCOUT Mini U 盘离线巡检包

- SCOUT 巡检期间完全离线，不连接 digital-twin。巡检结束后由 U 盘把 RFC 8493 BagIt
  目录复制到服务器 `inbox`，由本机 CLI `python -m app.cli import-package` 导入。
  公共 HTTP 不能触发导入，也不返回归档路径。
- 服务端依次执行 `inbox → staging → BagIt/SHA-256/语义校验 → archive → SQLite 事务物化`；
  失败包进入 quarantine，不产生半条 PatrolTask 或 TrajectoryPoint。
- 同一 `run_id` 与同一载荷哈希返回 `duplicate`；同一 `run_id` 的不同载荷返回 409。
- 正式交接契约是 `inspection-package-v2.schema.json`。`inspection_run` 只接受显式 v2；
  真实 v1 包被拒绝。v1 `run-bundle.schema.json` 仅保留给
  `synthetic_contract_fixture` 开发/迁移夹具。
- v2 轨迹必须已经按精确 Scene Version 与 `alignment_id` 对齐为 `scene_local_yup`，
  时间严格递增、序号连续，因此前端和 Three.js 继续复用现有回放接口。
- MCAP 可随包归档但不在服务器解析或作为连续原始视频播放。点云等展示产物可选，
  不承诺渲染。客户仪器数据不进入此契约。黄金包位于
  `data/contract-fixtures/inspection-package-v2-*`；v1 合成样本位于
  `data/contract-fixtures/scout-run-bagit-v1/`，明确不是现场或客户数据。
- 正式导入前须用本机 CLI 登记 Scene Version 与 Verified Alignment。Recorded Run 与
  Demonstration Run 在回放页分开；导入后为「现场实录，待验收」。
- 数据库与 inbox/staging/archive/quarantine 默认在 `data/var` 与 `data/imports`，
  启动时拒绝落在 `services/`、`apps/`、`packages/`、`docker/` 内。

## 5. SceneMetadata 与坐标系约定

`SceneMetadata`（`GET /api/scenes/{scene_id}/metadata`）是三维展示与巡检路线的
唯一声明来源：前端车辆轨迹/路线不得写死在组件中，必须从此对象动态加载。
字段含 `mesh_url` / `pointcloud_url`（相对站点根路径，离线可用）、
`route`（巡检路线有序路径点，车辆轨迹由此派生）、`bounds_min` / `bounds_max` 等。

坐标系统一约定：

- 业务层（API / Web / 契约）一律使用 `scene_local_yup`：右手系，**Y 轴向上，单位米**。
  `SceneMetadata` 中 `coordinate_system`、`units`、`up_axis` 分别固定为
  `scene_local_yup`、`m`、`Y`。
- twin-viewer 引擎公共 API 实测同为 Y-up（其 README 中的 Z-up 描述与实现不一致，
  已向 Grok 反馈）。为防御引擎后续变更，前端 `TwinViewerAdapter` 仍把
  Y-up → 引擎坐标 的转换集中在单点实现（当前为恒等映射，一行开关可启用
  `(x, y, z)_yup → (x, -z, y)_zup`）。其他模块不得各自为政。
- 与甲方 GIS/BIM 坐标系的对齐仍为 `pending_confirmation`，待确认后更新契约。

## 6. TwinViewer 契约边界

- 前端业务页面**不得直接调用 Three.js**，一切三维交互（加载场景、切换显示模式、
  车辆位姿、轨迹播放、设备/事件点位、拾取、相机控制）均经由
  `packages/contracts/src/twin-viewer.ts` 定义的接口
  （`TwinViewerProps` / `TwinViewerInputs` / `TwinViewerCallbacks`）。
- `packages/twin-viewer/**` 与 `assets/tunnel/**` 为 Grok 独占，业务方不得修改。
- 引擎交付前，由 `apps/web` 内的 `TwinViewerAdapter` + `MockViewer` 占位实现该接口；
  适配器同时把 §5 的坐标转换集中在单点实现（当前引擎公共 API 同为 Y-up，
  转换为恒等映射，一行开关即可切换到 Z-up 输入）。

## 7. 存储

- SQLite 单文件（默认 `data/var/twin.db`，可用环境变量 `TWIN_DB_PATH` 覆盖）。
- 建表与种子幂等：启动时 `CREATE TABLE IF NOT EXISTS`，仅当库为空时载入 `data/demo/`。
- 选型理由：离线原型、零依赖部署、可把整个演示打包为单文件。后续可平滑迁移 PostgreSQL
  （访问层已集中于 `db.py`）。

## 8. 数据出处（provenance）约定

未获得甲方协议支持是当前原型的默认状态，因此采用**机器可检查**的标记约定：

1. **字段级**：`domain.schema.json` 与 `openapi.yaml` 中每个属性带扩展键
   `x-provenance`，取值：
   - `protocol_supported`：甲方协议已明确支持（当前：无）。
   - `pending_confirmation`：业务上需要、但甲方协议未确认（如几何模型格式、处置人账号体系）。
   - `simulated`：原型自行虚构的演示数据（如检测置信度、事件照片）。
2. **对象级**：每个响应对象携带 `source_type`（simulation|replay|live_pending）与
   `provenance` 子对象：
   `{ "source": "simulation|replay|live_pending", "status": "simulated|pending_confirmation|confirmed" }`。
   当前所有对象均为 `simulated` 或 `pending_confirmation`，不存在 `confirmed`。
3. 字段升级为 `protocol_supported` 必须同时更新契约文件与实现，并在验收文档记录。

## 9. API 与 WebSocket 概览

REST 前缀 `/api`，详表见 `packages/contracts/openapi.yaml`：

- 场景：`GET /scenes`、`GET /scenes/{id}`、`GET /scenes/{id}/metadata`（SceneMetadata）
- 任务：`GET /tasks`、`GET /tasks/{id}`、`GET /tasks/{id}/trajectory`
- 事件：`GET /events`、`GET /events/{id}`、`PATCH /events/{id}`（复核状态）、
  `POST /events/export`（请求体 `EventExportRequest`，返回 CSV/JSON）
- 设备：`GET /devices`、`GET /devices/{id}/readings`（模拟曲线）
- 数据源：`GET /data-sources`
- 离线巡检包：`GET /imports`（只读台账，不含归档路径）；导入走本机 CLI
- 预留接入：`POST /live/ingest`（恒 501，live_pending）
- 健康：`GET /health`

WebSocket（事件格式见 `websocket-events.schema.json`）：

- `WS /ws/vehicle?task_id=`：车辆位置推送（simulation 实时模拟 / replay 回放）。
- `WS /ws/replay/{task_id}`：任务回放通道，支持客户端控制消息。

## 10. 错误与一致性约定

- 错误统一 `{ "error": { "code", "message", "details?" } }`；HTTP 语义码：
  400 参数错 / 404 不存在 / 409 状态冲突（如对 resolved 事件重复处置）/ 501 预留未实现。
- 时间一律 ISO 8601 UTC（`YYYY-MM-DDTHH:MM:SSZ`）。
- 坐标：统一 `scene_local_yup`（右手系，Y 轴向上，单位米），见 §5；
  与甲方 GIS/BIM 坐标系的对齐待确认。
- 幂等：事件状态修改按状态机校验，非法迁移返回 409；导出为只读操作。

## 11. 部署与运行

- 零外部服务依赖：`pip install -r services/api/requirements.txt` 后
  `uvicorn app.main:app` 即可运行（Python ≥ 3.11）。
- 环境变量：`TWIN_DB_PATH`（数据库路径）、`TWIN_IMPORT_ROOT`（本地导入目录）、
  `TWIN_SIM_TICK_MS`（模拟推送周期，默认 1000）、`TWIN_SEED`（模拟随机种子，默认 42）。

## 12. 测试

- 测试位于 `services/api/tests/`（本阶段 `tests/` 目录不在后端负责人所有权内）。
- 覆盖：全部 REST 端点、事件状态机、WS 车辆推送与回放控制、模拟曲线确定性、
  BagIt/SHA-256 校验、重复导入、冲突与失败隔离、轨迹物化、live_pending 接口恒 501、
  契约一致性抽样（响应包含 `source_type`/`provenance` 且字段在契约中已定义）。
