# API 说明

本文档按 `packages/contracts/openapi.yaml`（API version 0.2.0）逐端点说明。
对象字段的完整定义见 [DATA_FIELDS.md](DATA_FIELDS.md) 与
`packages/contracts/domain.schema.json`。

## 通用约定

- **Base URL**：`http://localhost:8000/api`（REST 前缀 `/api`）
- **坐标系**：统一 `scene_local_yup`（右手系，Y 轴向上，单位米）
- **时间**：一律 ISO 8601 UTC（`YYYY-MM-DDTHH:MM:SSZ`）
- **错误格式**：统一为

  ```json
  { "error": { "code": "not_found", "message": "...", "details": {} } }
  ```

  HTTP 语义码：400 参数错误 / 404 资源不存在 / 409 状态冲突（非法状态迁移）/ 501 预留未实现。
- **数据出处**：每个响应对象携带 `source_type`（simulation|replay|live_pending）与
  `provenance` 子对象（`{ "source", "status" }`），当前不存在 `status = confirmed` 的对象。
- **交互文档**：Swagger UI 位于 `http://localhost:8000/docs`。

---

## system

### GET /health — 健康检查

```bash
curl http://localhost:8000/api/health
```

响应 `200`：

```json
{ "status": "ok", "version": "0.1.0" }
```

## scenes

### GET /scenes — 场景列表

```bash
curl http://localhost:8000/api/scenes
```

响应 `200`：Scene 数组。示例（摘自 `data/demo/scenes.json`，服务端响应另附
`source_type`/`provenance` 字段）：

```json
[
  {
    "id": "scene-001",
    "name": "庙岭隧道 K12+300—K12+900 段",
    "description": "山岭公路隧道示范段，单洞双向，布设固定传感器与巡检车路线",
    "status": "active",
    "geometry_ref": "models/scene-001.glb",
    "bounds_min": { "x": 0.0, "y": -3.25, "z": -7.0 },
    "bounds_max": { "x": 600.0, "y": 3.25, "z": 2.5 },
    "length_m": 600.0,
    "created_at": "2026-07-15T02:00:00Z",
    "source_type": "simulation",
    "provenance": { "source": "simulation", "status": "simulated" }
  }
]
```

### GET /scenes/{scene_id} — 场景详情（含空间绑定）

| 参数 | 位置 | 说明 |
|---|---|---|
| `scene_id` | path | 场景编号 |

```bash
curl http://localhost:8000/api/scenes/scene-001
```

响应 `200`：Scene + `spatial_bindings`（SpatialBinding 数组）；`404` 场景不存在。

### GET /scenes/{scene_id}/metadata — 场景元数据

三维资产引用、坐标系声明与巡检路线。前端车辆轨迹/路线不得写死，必须从此接口动态加载。

```bash
curl http://localhost:8000/api/scenes/scene-001/metadata
```

响应 `200`（摘自 `data/demo/scene-metadata.json`，route 仅节选自 12 个路径点中的首尾）：

```json
{
  "scene_id": "scene-001",
  "name": "庙岭隧道 K12+300—K12+900 段",
  "coordinate_system": "scene_local_yup",
  "units": "m",
  "up_axis": "Y",
  "bounds_min": { "x": -119.0, "y": -34.0, "z": -110.0 },
  "bounds_max": { "x": 122.0, "y": 35.0, "z": 34.0 },
  "length_m": 241.0,
  "mesh_url": "/tunnel/liris/tunnel_mesh.obj",
  "pointcloud_url": "/tunnel/liris/tunnel_pointcloud.ply",
  "route": [
    { "x": -100.0, "y": -25.0, "z": -35.0 },
    { "x": 110.0, "y": -25.0, "z": -35.0 }
  ],
  "source_type": "simulation",
  "provenance": { "source": "simulation", "status": "simulated" }
}
```

`404` 场景不存在。

## tasks

### GET /tasks — 巡检任务列表

| 参数 | 位置 | 说明 |
|---|---|---|
| `scene_id` | query | 按场景过滤 |
| `status` | query | `pending` / `running` / `completed` / `aborted` |
| `mode` | query | `simulation` / `replay` / `live_pending` |

```bash
curl "http://localhost:8000/api/tasks?status=completed"
```

响应 `200`：PatrolTask 数组（按 `actual_end`、否则 `planned_start` 倒序）。示例（摘自 `data/demo/tasks.json`）：

```json
[
  {
    "id": "task-replay-001",
    "scene_id": "scene-001",
    "name": "8月1日例行巡检（回放）",
    "mode": "replay",
    "status": "completed",
    "planned_start": "2026-08-01T09:00:00Z",
    "actual_start": "2026-08-01T09:00:12Z",
    "actual_end": "2026-08-01T09:02:11Z",
    "distance_m": 595.0,
    "event_count": 4,
    "source_type": "replay",
    "provenance": { "source": "replay", "status": "simulated" }
  }
]
```

### GET /tasks/{task_id} — 任务详情

```bash
curl http://localhost:8000/api/tasks/task-replay-001
```

响应 `200`：PatrolTask；`404` 任务不存在。

### GET /tasks/{task_id}/trajectory — 任务轨迹（回放数据源）

| 参数 | 位置 | 说明 |
|---|---|---|
| `task_id` | path | 运行身份（`run_id`） |
| `from_seq` | query | 起始序号，默认 0 |
| `limit` | query | 条数上限，1–1000000，默认 10000。前端应翻页直到取完整 Replay Trajectory。 |

```bash
curl "http://localhost:8000/api/tasks/task-replay-001/trajectory?from_seq=0&limit=2"
```

响应 `200`：TrajectoryPoint 数组（按 seq 升序。种子数据 task-replay-001 共 157 点，
2026-08-01T09:00:12Z 至 09:02:48Z）。示例（摘自 `data/demo/trajectories.json`）：

```json
[
  {
    "task_id": "task-replay-001",
    "seq": 0,
    "timestamp": "2026-08-01T09:00:12Z",
    "position": { "x": -92.188, "y": 23.275, "z": 23.557 },
    "heading_deg": 334.1,
    "speed_mps": 0.973,
    "source_type": "replay",
    "provenance": { "source": "replay", "status": "simulated" }
  },
  {
    "task_id": "task-replay-001",
    "seq": 1,
    "timestamp": "2026-08-01T09:00:13Z",
    "position": { "x": -91.323, "y": 23.129, "z": 23.136 },
    "heading_deg": 334.1,
    "speed_mps": 0.973,
    "source_type": "replay",
    "provenance": { "source": "replay", "status": "simulated" }
  }
]
```

`404` 任务不存在。

## events

### GET /events — 异常事件查询

| 参数 | 位置 | 说明 |
|---|---|---|
| `scene_id` / `task_id` | query | 按场景 / 任务过滤 |
| `status` | query | `open` / `acknowledged` / `resolved` / `false_positive` |
| `severity` | query | `low` / `medium` / `high` / `critical` |
| `type` | query | `crack` / `water_leakage` / `spalling` / `corrosion` / `equipment_fault` / `obstacle` |
| `limit` | query | 1–1000，默认 100 |
| `offset` | query | 分页偏移，默认 0 |

```bash
curl "http://localhost:8000/api/events?status=open"
```

响应 `200`：DetectionEvent 数组（按检测时间倒序）。示例（摘自 `data/demo/events.json`）：

```json
[
  {
    "id": "evt-0001",
    "scene_id": "scene-001",
    "task_id": "task-replay-001",
    "type": "crack",
    "severity": "high",
    "status": "open",
    "position": { "x": -60.0, "y": -10.0, "z": -34.0 },
    "description": "边墙纵向裂缝，长约 1.8m，宽约 2.1mm",
    "confidence": 0.87,
    "image_ref": "images/evt-0001.jpg",
    "detected_at": "2026-08-01T09:00:22Z",
    "handled_by": null,
    "handled_at": null,
    "handle_comment": null,
    "source_type": "replay",
    "provenance": { "source": "replay", "status": "simulated" }
  }
]
```

### GET /events/{event_id} — 事件详情

```bash
curl http://localhost:8000/api/events/evt-0001
```

响应 `200`：DetectionEvent；`404` 事件不存在。

### PATCH /events/{event_id} — 修改事件复核状态

状态机：`open → acknowledged → resolved | false_positive`；非法迁移返回 `409`。

请求体（EventStatusUpdate）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | string | 是 | `acknowledged` / `resolved` / `false_positive` |
| `comment` | string | 否 | 复核意见 |
| `handled_by` | string | 否 | 处置人（账号体系待确认，`pending_confirmation`） |

```bash
curl -X PATCH http://localhost:8000/api/events/evt-0001 \
  -H 'Content-Type: application/json' \
  -d '{"status": "acknowledged", "comment": "已通知养护班组现场确认", "handled_by": "operator-chen"}'
```

响应 `200`：更新后的 DetectionEvent（`handled_by`/`handled_at`/`handle_comment` 已写入）。
`404` 事件不存在；`409` 非法状态迁移（如对 `resolved` 事件重复处置）。

### POST /events/export — 事件导出（CSV 或 JSON）

请求体（EventExportRequest）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `format` | string | 是 | `csv` / `json` |
| `scene_id` / `task_id` / `status` / `severity` / `type` | string | 否 | 过滤条件，枚举同 GET /events |

```bash
curl -X POST http://localhost:8000/api/events/export \
  -H 'Content-Type: application/json' -d '{"format": "csv", "status": "open"}'
```

响应 `200`：`format=csv` 返回 `text/csv` 文件；`format=json` 返回 DetectionEvent 数组。
导出为只读操作。

## devices

### GET /devices — 传感器设备列表

| 参数 | 位置 | 说明 |
|---|---|---|
| `scene_id` | query | 按场景过滤 |
| `type` | query | `delamination` / `displacement` / `convergence` / `stress` / `temperature` / `humidity` / `gas` |

```bash
curl "http://localhost:8000/api/devices?type=delamination"
```

响应 `200`：SensorDevice 数组。示例（摘自 `data/demo/devices.json`）：

```json
[
  {
    "id": "dev-delam-001",
    "scene_id": "scene-001",
    "name": "K12+350 拱顶离层仪",
    "type": "delamination",
    "unit": "mm",
    "position": { "x": -80.0, "y": 30.0, "z": -35.0 },
    "status": "online",
    "source_type": "simulation",
    "provenance": { "source": "simulation", "status": "simulated" }
  }
]
```

### GET /devices/{device_id}/readings — 设备模拟读数曲线

确定性生成（正弦 + 按设备 id 定种子的噪声）：相同参数必得相同结果。读数均为 `simulated`，
不落库，按请求即时生成。

| 参数 | 位置 | 说明 |
|---|---|---|
| `device_id` | path | 设备编号 |
| `from` | query | 起始时间（ISO 8601），缺省为 `to` 前 1 小时 |
| `to` | query | 结束时间，缺省为当前时间 |
| `interval_sec` | query | 采样间隔秒，1–3600，默认 60 |

```bash
curl "http://localhost:8000/api/devices/dev-delam-001/readings?interval_sec=60"
```

响应 `200`：SensorReading 数组（按时间升序），元素形如：

```json
{ "device_id": "dev-delam-001", "timestamp": "2026-08-07T06:21:00Z", "value": 1.23, "quality": "good",
  "source_type": "simulation", "provenance": { "source": "simulation", "status": "simulated" } }
```

`404` 设备不存在。

## data-sources

### GET /data-sources — 数据来源状态

```bash
curl http://localhost:8000/api/data-sources
```

响应 `200`：三种数据模式的状态数组（摘自 `services/api/app/routers/datasources.py`）：

```json
[
  { "mode": "simulation", "name": "内置模拟器", "status": "active",
    "last_update": "2026-08-07T07:21:27Z", "message": null,
    "source_type": "simulation", "provenance": { "source": "simulation", "status": "simulated" } },
  { "mode": "replay", "name": "历史数据回放", "status": "active",
    "last_update": "2026-08-07T07:21:27Z", "message": null,
    "source_type": "replay", "provenance": { "source": "replay", "status": "simulated" } },
  { "mode": "live_pending", "name": "甲方实时数据接入", "status": "reserved",
    "last_update": null, "message": "预留接口，待甲方协议确认后启用",
    "source_type": "live_pending", "provenance": { "source": "live_pending", "status": "pending_confirmation" } }
]
```

`live_pending` 源固定为 `reserved`（预留，未接入），`last_update` 为 null。

## live（预留）

### POST /live/ingest — 甲方实时数据接入（恒 501）

接口信封已定义，服务端**一律返回 501**，待甲方协议确认后启用，不虚构真实接入。

请求体（LiveIngestEnvelope）：`source_id`（string）、`timestamp`（date-time）、
`payload`（object，原始报文结构待甲方协议定义），整体 `pending_confirmation`。

```bash
curl -X POST http://localhost:8000/api/live/ingest \
  -H 'Content-Type: application/json' \
  -d '{"source_id": "gw-01", "timestamp": "2026-08-07T07:00:00Z", "payload": {}}'
```

响应 `501`：

```json
{ "error": { "code": "not_implemented", "message": "live 实时数据接入为预留接口，待甲方协议确认后启用" } }
```

## imports（只读台账）

### GET /imports — 已导入运行的安全状态投影

导入只能通过服务器本机 CLI，公共 HTTP 不能触发导入，也不返回归档路径。
操作员先把 RFC 8493 BagIt 目录完整复制到 `$TWIN_IMPORT_ROOT/inbox/<bundle_name>/`，
再登记场景并导入。正式 `inspection_run` 只接受 Formal Inspection Package v2。

```bash
cp -a /media/usb/inspection-run-run-001.bag data/imports/inbox/
cd services/api
uv run python -m app.cli register-scene --file ../../data/contract-fixtures/scene-registration-scene-001-v1.json --operator-label "现场操作员"
uv run python -m app.cli import-package inspection-run-run-001.bag
```

服务端先复制到 staging，再校验 BagIt 结构、全部 SHA-256、运行 manifest 与轨迹语义；成功后
归档并在一个 SQLite 事务中物化任务和轨迹。相同 `run_id` 与相同载荷返回 `duplicate`；
相同 `run_id` 的不同载荷返回 `409 run_id_conflict` 并进入 quarantine。成功后可直接读取：

```bash
curl http://localhost:8000/api/tasks/golden-run-v2-completed
curl http://localhost:8000/api/tasks/golden-run-v2-completed/trajectory
curl http://localhost:8000/api/tasks/golden-run-v2-completed/display-trajectory
curl "http://localhost:8000/api/scenes/scene-001/metadata?scene_version_id=scene-001-v1"
curl http://localhost:8000/api/scene-versions/scene-001-v1
```

`GET /tasks/{task_id}/display-trajectory` 返回确定性 Display Trajectory，保留首点、末点和
验收地标，并带有“不是原始证据”的说明。它不是浏览器实时渲染 1,000,000 点的承诺。
回放时间轴仍使用完整 Replay Trajectory。公共接口不返回验收图片或归档路径。

`completed_with_exceptions` 在任务 API 和回放页面中保持独立语义。Recorded Run 标记为
`run_kind=recorded` 且 `acceptance_state=pending_acceptance`（现场实录，待验收），
导入成功不会写成已验收或 confirmed。`GET /api/imports` 不返回归档路径。
MCAP 作为不透明原始记录归档，Digital Twin 不解析 MCAP、不承诺点云渲染、不接收客户
仪器数据。

---

## WebSocket

统一信封 `{ "type": string, "ts": date-time, "payload": object }`。
消息格式以 `packages/contracts/websocket-events.schema.json` 为准。

### WS /ws/vehicle?task_id= — 车辆位置推送

订阅指定任务（simulation 实时模拟 / replay 回放）的车辆状态推送。

### WS /ws/replay/{task_id} — 任务回放通道

按原始时间轴 × 倍速推送轨迹点；回放为只读，不改变任务与事件的历史状态。

### 服务端 → 客户端消息类型

| `type` | payload 类型 | 说明 |
|---|---|---|
| `connection.ack` | `{ server_time, channel: "vehicle"\|"replay", task_id? }` | 连接确认 |
| `vehicle.state` | `VehicleState` | 车辆瞬时状态（位置、航向、速度、电量） |
| `replay.started` | `{ task_id, total_points, speed }` | 回放开始 |
| `replay.point` | `{ point: TrajectoryPoint, progress: 0–1 }` | 回放轨迹点与进度 |
| `replay.finished` | `{ task_id, total_points }` | 回放结束 |
| `event.created` | `DetectionEvent` | 新检测事件 |
| `datasource.status` | `DataSourceStatus` | 数据源状态变更 |
| `error` | `{ code, message }` | 错误 |

示例：

```json
{ "type": "vehicle.state", "ts": "2026-08-07T07:21:28Z",
  "payload": { "task_id": "task-sim-001", "timestamp": "2026-08-07T07:21:28Z",
    "position": { "x": 12.5, "y": -25.0, "z": -35.0 }, "heading_deg": 90.0, "speed_mps": 4.96,
    "battery_pct": 86.0, "source_type": "simulation",
    "provenance": { "source": "simulation", "status": "simulated" } } }
```

### 客户端 → 服务端消息（仅回放通道）

`type` 固定为 `replay.control`，payload：

| 字段 | 类型 | 说明 |
|---|---|---|
| `action` | string | 必填：`pause` / `resume` / `seek` / `speed` |
| `seq` | integer ≥ 0 | `action=seek` 时必填，跳转到指定轨迹点序号 |
| `speed` | number (0, 100] | `action=speed` 时必填，回放倍速，默认 1.0 |

示例：

```json
{ "type": "replay.control", "payload": { "action": "seek", "seq": 60 } }
{ "type": "replay.control", "payload": { "action": "speed", "speed": 4 } }
```
