# 数据字段说明

本文档逐个说明 10 个核心领域对象的字段。字段定义以冻结契约
`packages/contracts/src/domain.ts` / `packages/contracts/domain.schema.json` 为准。

## 通用约定

### 来源标记（x-provenance）

契约中每个字段带 `x-provenance` 标记，取值三种：

| 标记 | 含义 |
|---|---|
| `protocol_supported` | 甲方协议已明确支持（当前：无业务字段，仅 source_type/mode 等枚举本身） |
| `pending_confirmation` | 业务上需要、但甲方协议未确认（如坐标数值、单位、处置人账号体系） |
| `simulated` | 原型自行虚构的演示数据（如检测置信度、事件照片） |

每个响应对象另携带对象级出处：`source_type`（simulation|replay|live_pending）与
`provenance: { source, status }`（status ∈ simulated|pending_confirmation|confirmed，
当前不存在 confirmed 对象）。下表"来源"列即字段级 x-provenance，简写为
S = simulated，P = pending_confirmation。

### 三种 source_type 的含义

| source_type | 含义 |
|---|---|
| `simulation` | 后端内置模拟器生成的数据（车辆轨迹、事件、传感器曲线），固定种子、可复现 |
| `replay` | `data/demo/` 中的历史任务回放数据 |
| `live_pending` | 甲方实时数据接入（预留）。**不存在"真实数据已接入"状态**，该模式仅有状态记录与恒 501 的预留接口 |

### 坐标系约定

- 业务层（API / Web / 契约）统一使用 `scene_local_yup`：右手系，**Y 轴向上，单位米**。
  `SceneMetadata` 中 `coordinate_system` / `units` / `up_axis` 固定为
  `scene_local_yup` / `m` / `Y`。
- **引擎边界转换**：LIRIS 原始资产为右手系 Z-up（Blender 导出）；Grok 的 twin-viewer
  引擎在加载时按 `assets/tunnel/liris/scene-metadata.json` 的 `transformMatrix`
  转为 Y-up 米制，其公共 API 实测同为 Y-up。前端 `TwinViewerAdapter` 把
  "Y-up → 引擎坐标"的转换集中在单点（当前为恒等映射，预留一行开关可切换到
  `(x, y, z)_yup → (x, -z, y)_zup`），其他模块不得各自为政。
- 与甲方 GIS/BIM 坐标系的对齐仍为 `pending_confirmation`。

### 时间与错误

- 时间一律 ISO 8601 UTC（`YYYY-MM-DDTHH:MM:SSZ`）。
- 错误统一 `{ "error": { "code", "message", "details?" } }`。

---

## 1. Scene — 地下工程场景

| 字段 | 类型 | 含义 | 单位 | 来源 |
|---|---|---|---|---|
| `id` | string | 场景编号 | — | S |
| `name` | string | 场景名称（如"庙岭隧道 K12+300—K12+900 段"） | — | P |
| `description` | string? | 描述 | — | S |
| `status` | enum | `active` / `archived` | — | S |
| `geometry_ref` | string\|null | 几何模型引用 | — | P |
| `bounds_min` / `bounds_max` | Position3D | 空间包围盒下/上界 | m | P（Position3D 各分量） |
| `length_m` | number | 场景纵向长度 | m | P |
| `created_at` | date-time | 创建时间 | — | S |
| `source_type` / `provenance` | — | 通用出处字段 | — | — |

`SceneDetail` = Scene + `spatial_bindings: SpatialBinding[]`。

## 2. PatrolTask — 巡检任务

状态机：`pending → running → completed | aborted`。

| 字段 | 类型 | 含义 | 单位 | 来源 |
|---|---|---|---|---|
| `id` | string | 任务编号 | — | S |
| `scene_id` | string | 所属场景 | — | S |
| `name` | string | 任务名称 | — | S |
| `mode` | enum | 数据模式（simulation/replay/live_pending） | — | protocol_supported |
| `status` | enum | `pending` 待启动 / `running` 进行中 / `completed` 已完成 / `aborted` 已中止 | — | S |
| `planned_start` | date-time\|null | 计划开始时间 | — | S |
| `actual_start` / `actual_end` | date-time\|null | 实际开始/结束时间 | — | S |
| `distance_m` | number\|null | 累计里程 | m | S |
| `event_count` | integer | 关联异常候选条数 | 条 | S |

## 3. VehicleState — 巡检车瞬时状态

由 WebSocket 周期推送（`vehicle.state`）。

| 字段 | 类型 | 含义 | 单位 | 来源 |
|---|---|---|---|---|
| `task_id` | string | 所属任务 | — | S |
| `timestamp` | date-time | 采样时刻 | — | S |
| `position` | Position3D | 位置（scene_local_yup） | m | P |
| `heading_deg` | number | 航向角，[0, 360)，绕 +Y 轴 | 度 | P |
| `speed_mps` | number | 速度 | m/s | P |
| `battery_pct` | number\|null | 电量百分比（车辆侧协议待确认） | % | P |

## 4. TrajectoryPoint — 轨迹点

任务的有序历史位置序列，回放的数据源（REST 批量获取 + WS 回放推送）。

| 字段 | 类型 | 含义 | 单位 | 来源 |
|---|---|---|---|---|
| `task_id` | string | 所属任务 | — | S |
| `seq` | integer | 任务内单调递增序号 | — | S |
| `timestamp` | date-time | 采样时刻 | — | S |
| `position` | Position3D | 位置 | m | P |
| `heading_deg` | number\|null | 航向角 | 度 | P |
| `speed_mps` | number\|null | 速度 | m/s | P |

## 5. DetectionEvent — 异常检测事件

异常候选**不等于**已确认灾害，需经复核状态机处置。

| 字段 | 类型 | 含义 | 单位 | 来源 |
|---|---|---|---|---|
| `id` | string | 事件编号 | — | S |
| `scene_id` / `task_id` | string | 所属场景 / 发现任务 | — | S |
| `type` | enum | 异常类型（见下表，最终以甲方检测算法输出为准） | — | P |
| `severity` | enum | 严重程度（见下表） | — | P |
| `status` | enum | 复核状态（见状态机一节） | — | S |
| `position` | Position3D | 发生位置 | m | P |
| `description` | string? | 描述（如"边墙纵向裂缝，长约 1.8m，宽约 2.1mm"） | — | S |
| `confidence` | number\|null | 检测置信度 0–1（模拟算法输出） | — | S |
| `image_ref` | string\|null | 现场图片引用（原型为占位图） | — | S |
| `detected_at` | date-time | 检测时间 | — | S |
| `handled_by` | string\|null | 处置人（账号体系待确认） | — | P |
| `handled_at` | date-time\|null | 处置时间 | — | S |
| `handle_comment` | string\|null | 处置意见 | — | S |

异常类型中英对照（`apps/web/src/services/api/adapters.ts` EVENT_TYPE_LABELS）：

| type | 中文 |
|---|---|
| `crack` | 衬砌裂缝 |
| `water_leakage` | 渗漏水 |
| `spalling` | 混凝土剥落 |
| `corrosion` | 钢筋锈蚀 |
| `equipment_fault` | 设备故障 |
| `obstacle` | 异物侵限 |

严重程度中英对照（`apps/web/src/utils/labels.ts` SEVERITY_LABELS）：

| severity | 中文 |
|---|---|
| `low` | 低风险 |
| `medium` | 中风险 |
| `high` | 高风险 |
| `critical` | 严重 |

### 事件复核状态机与 UI 中文映射

状态机：`open → acknowledged → resolved | false_positive`；非法迁移返回 409。

| 后端 status | 契约语义中文 | 复核页 UI 显示（adapters 映射） |
|---|---|---|
| `open` | 待复核 | 待专家复核（pending） |
| `acknowledged` | 已确认 | 已复核确认（verified） |
| `resolved` | 已闭环 | 已归档完成（rectified） |
| `false_positive` | 已排除 | 已判定误报（false_positive） |

复核页操作约束：仅 `open` 可执行"确认缺陷并派单"（→ acknowledged）；
仅 `acknowledged` 可执行"判定为误报"（→ false_positive）。

## 6. SensorDevice — 监测设备

| 字段 | 类型 | 含义 | 单位 | 来源 |
|---|---|---|---|---|
| `id` | string | 设备编号（如 dev-delam-001） | — | S |
| `scene_id` | string | 所属场景 | — | S |
| `name` | string | 设备名称（如"K12+350 拱顶离层仪"） | — | S |
| `type` | enum | 设备类型（见下表，以甲方设备清单为准） | — | P |
| `unit` | string | 测量单位 | mm / MPa / °C / % 等 | P |
| `position` | Position3D? | 空间位置 | m | P |
| `status` | enum | `online` 在线 / `offline` 离线 | — | S |

设备类型中英对照（`apps/web/src/utils/labels.ts` DEVICE_TYPE_LABELS）：

| type | 中文 |
|---|---|
| `delamination` | 离层仪 |
| `displacement` | 位移计 |
| `convergence` | 收敛计 |
| `stress` | 应力计 |
| `temperature` | 温度传感器 |
| `humidity` | 湿度传感器 |
| `gas` | 气体传感器 |

## 7. SensorReading — 传感器读数

模拟曲线为确定性正弦 + 噪声（按设备 id 定种子）：相同 from/to/interval_sec 必得相同结果；
读数不落库，按请求即时生成，均为 simulated。

| 字段 | 类型 | 含义 | 单位 | 来源 |
|---|---|---|---|---|
| `device_id` | string | 设备编号 | — | S |
| `timestamp` | date-time | 采样时刻 | — | S |
| `value` | number | 读数值 | 随设备 unit | S |
| `quality` | enum? | `good` / `uncertain` / `bad`（数据质量标记，待甲方确认） | — | P |

## 8. SpatialBinding — 空间绑定

对象（设备/事件/车辆）到三维场景坐标的绑定关系。

| 字段 | 类型 | 含义 | 单位 | 来源 |
|---|---|---|---|---|
| `id` | string | 绑定编号 | — | S |
| `scene_id` | string | 所属场景 | — | S |
| `target_type` | enum | `sensor_device` / `detection_event` / `vehicle` | — | S |
| `target_id` | string | 目标对象编号 | — | S |
| `position` | Position3D | 绑定坐标 | m | P |
| `coordinate_system` | string? | 坐标系标识，固定 `scene_local_yup`；与甲方 GIS/BIM 对齐待确认 | — | P |

## 9. DataSourceStatus — 数据来源状态

| 字段 | 类型 | 含义 | 来源 |
|---|---|---|---|
| `mode` | enum | simulation / replay / live_pending | protocol_supported |
| `name` | string | 显示名（内置模拟器 / 历史数据回放 / 甲方实时数据接入） | S |
| `status` | enum | `active` / `standby` / `reserved` / `unavailable`；live_pending 固定 `reserved` | S |
| `last_update` | date-time\|null | 最近更新时间；live_pending 固定为 null | S |
| `message` | string\|null | 附加说明（如"预留接口，待甲方协议确认后启用"） | S |

## 10. SceneMetadata — 场景元数据

三维展示与巡检路线的唯一声明来源：前端车辆轨迹/路线不得写死，必须从此对象动态加载。

| 字段 | 类型 | 含义 | 单位 | 来源 |
|---|---|---|---|---|
| `scene_id` | string | 场景编号 | — | S |
| `name` | string | 场景名称 | — | P |
| `coordinate_system` | 常量 | 固定 `scene_local_yup` | — | — |
| `units` | 常量 | 固定 `m` | — | — |
| `up_axis` | 常量 | 固定 `Y` | — | — |
| `bounds_min` / `bounds_max` | Position3D | 包围盒（scene-001 由引擎 Z-up 包围盒经 `(x,-z,y)` 逆映射得到） | m | P |
| `length_m` | number | 场景纵向长度（scene-001 为 241.0） | m | P |
| `mesh_url` | string\|null | 三维网格 URL（`/tunnel/liris/tunnel_mesh.obj`，相对站点根，离线可用） | — | S |
| `pointcloud_url` | string\|null | 点云 URL（`/tunnel/liris/tunnel_pointcloud.ply`） | — | S |
| `route` | Position3D[] | 巡检路线有序路径点（车辆轨迹由此派生；scene-001 共 12 点） | m | S |
| `description` | string? | 描述 | — | S |

---

## 附：通用子结构

**Position3D**：`{ x: number, y: number, z: number }` — scene_local_yup 坐标，单位米，
各分量 `pending_confirmation`。

**Provenance**：`{ source: SourceType, status: "simulated" | "pending_confirmation" | "confirmed" }`
— 当前所有对象均为 simulated 或 pending_confirmation，不存在 confirmed。

**EventStatusUpdate**（PATCH /events/{id} 请求体）：`status`（acknowledged|resolved|false_positive，
S）、`comment`（string，S）、`handled_by`（string，P，账号体系待确认）。

**EventExportRequest**（POST /events/export 请求体）：`format`（csv|json，必填）+
可选过滤 `scene_id` / `task_id` / `status` / `severity` / `type`。

**LiveIngestEnvelope**（POST /live/ingest 请求体，整体 P）：`source_id`、`timestamp`、`payload`
（原始报文结构待甲方协议定义）。服务端恒返回 501。
