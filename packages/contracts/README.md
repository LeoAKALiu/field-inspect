# @digital-twin/contracts

前后端共享的 TypeScript 类型、JSON Schema 与 OpenAPI 契约：

- `domain.schema.json` / `src/domain.ts`：数字孪生领域对象 v2（冻结）+ v0.3 领域扩展（增量）；
- `websocket-events.schema.json`：WebSocket 信封；
- `inspection-package-v2.schema.json` / `src/inspection-package.ts`：正式巡检包 v2（Jetson 与 Digital Twin 的唯一正式交接契约）；
- `run-bundle.schema.json` / `src/run-bundle.ts`：离线巡检包语义契约 v1，仅用于明确标记的开发或迁移夹具，不承担生产兼容义务；
- `openapi.yaml`：REST 接口。

## v0.3 领域扩展（contract_version "0.3"）

v0.3 在不改动任何 v2 对象的前提下新增两个仓库（`LeoAKALiu/digital-twin` 与
`LeoAKALiu/scout_mini_ws`）共同使用的领域对象（domain.schema.json `$defs` / domain.ts
第 11–14 节 / openapi.yaml 组件 / `services/api/app/domain_contract.py` 边界模型）：

| 对象 | 说明 |
|---|---|
| `InstrumentObservation` | 仪器观测：稳定 ID、运行 ID、采集时间、归一化图像区域、检测器种类/版本、置信度、标定引用与逐对象血缘 |
| `InstrumentLocalization` | 仪器定位：观测 → 场景位姿，`matched / unmatched / needs_review` |
| `AssetMatch` | 资产匹配：定位 → 登记仪器资产，`matched / unmatched / needs_review`，与定位相互独立 |
| `InstrumentReading` | 命名指标读数：`deep_base`（深基点）/ `shallow_base`（浅基点）/ `delta`（差值），不复用单一匿名 value |
| `InspectionStation` | 巡检站：独立稳定 ID、目标位姿、注册仪器；不等同于基准 Tag |
| `StationAttempt` | 站点尝试/结果：`success / failed / skipped` + 显式原因码；`attempt_seq > 1` 即 retry，可追溯 |
| `InspectionRunOutcome` | 运行结果溯源：`completed / completed_with_exceptions / aborted` + skip/retry 汇总 |
| `DataLineage` | 逐对象数据血缘：`synthetic_fixture / simulation / replay / live_pending` |

升级说明与语义边界：

- 每个 v0.3 对象载荷携带 `contract_version: "0.3"`，其他契约版本一律拒绝；
  v2 对象与 v0.2 simulation/replay API、WebSocket、页面保持不变。
- **`marker_proxy` 不等于通用图像识别**：它只是以预登记基准标记（AprilTag 等）为代理的
  检测器；其结果不构成已确认的客户设备 ID，`unmatched` / `needs_review` 也不产生任何
  确认 ID。契约不包含连续原始视频展示或上传。
- 定位与资产匹配是两个独立对象，可分别复核与撤销。
- 黄金夹位于 `data/contract-fixtures/instrument-v0.3/`（含合法与非法夹具），合法夹具须
  同时通过 JSON Schema 与 Python 边界模型两个公共边界。

正式 `inspection_run` 只接受显式 `schema_version: "2.0"`。v2 包必须是完整 RFC 8493 BagIt 1.0
目录，禁止 `fetch.txt`，并携带精确 Scene Version、已验证对齐证据 SHA-256、完整 Replay Trajectory
和可复核的 ArUco 验收证据。黄金包位于 `data/contract-fixtures/inspection-package-v2-*`。

Digital Twin 不解析 MCAP、不承诺点云渲染、不接收客户仪器数据，也不提供在线上传入口。
包通过可移动介质复制到服务器本地 inbox 后导入。v1 `source_kind=synthetic_contract_fixture`
夹具不得宣称为客户或现场数据。
