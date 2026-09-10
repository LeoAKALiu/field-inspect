# v0.3 仪器观测领域契约黄金夹具

共享 v0.3 领域契约（`packages/contracts/domain.schema.json` 的 v0.3 `$defs`，
`contract_version: "0.3"`）的黄金夹具。全部为**显式合成的开发/测试数据**
（lineage `source=synthetic_fixture` 或 `simulation`），不含客户数据、现场影像、
连续视频或原始 MCAP；不得宣称任何夹具为现场或客户数据。

所有合法夹具必须能通过两个公共边界：

1. JSON Schema（Draft 2020-12）：`packages/contracts/domain.schema.json#/$defs/<Def>`；
2. Python 边界模型：`services/api/app/domain_contract.py` 中的 Pydantic 模型。

非法夹具（`malformed-*`、`unknown-*`）在两个边界都必须被拒绝。

## 夹具与 $defs 对照

| 文件 | $defs | 场景 |
|---|---|---|
| `observation-marker-proxy-simulation.json` | InstrumentObservation | simulation 血缘的 marker_proxy 观测 |
| `observation-synthetic-fixture.json` | InstrumentObservation | synthetic_fixture 血缘观测 |
| `localization-matched.json` | InstrumentLocalization | matched（带位姿与残差） |
| `localization-unmatched.json` | InstrumentLocalization | unmatched（位姿必须为 null） |
| `localization-needs-review.json` | InstrumentLocalization | needs_review（残差超限待复核） |
| `asset-match-matched.json` | AssetMatch | matched（唯一登记资产） |
| `asset-match-unmatched.json` | AssetMatch | unmatched（无候选） |
| `asset-match-needs-review.json` | AssetMatch | needs_review（候选 ≥1，无确认 ID） |
| `reading-historical-replay.json` | InstrumentReading | 历史回放读数（deep_base/shallow_base/delta 命名指标） |
| `reading-expired.json` | InstrumentReading | 过期读数（valid_until 已过，quality=uncertain） |
| `station-registered.json` | InspectionStation | 注册仪器的巡检站（stn-001） |
| `attempt-failed-then-retried.json` | StationAttempt | stn-001 第 1 次尝试失败（触发 retry） |
| `attempt-success-after-retry.json` | StationAttempt | stn-001 第 2 次尝试成功 |
| `attempt-skipped.json` | StationAttempt | stn-002 显式跳过（operator_skipped） |
| `run-outcome-completed.json` | InspectionRunOutcome | 无例外完成 |
| `run-outcome-completed-with-exceptions.json` | InspectionRunOutcome | 带例外完成（skip + retry 可追溯） |
| `run-outcome-aborted.json` | InspectionRunOutcome | 中止（safety_stop） |
| `malformed-observation-missing-confidence.json` | InstrumentObservation | 缺失必填 confidence → 拒绝 |
| `malformed-reading-empty-metrics.json` | InstrumentReading | 空命名指标集合 → 拒绝 |
| `malformed-localization-unmatched-with-position.json` | InstrumentLocalization | unmatched 却携带位姿 → 拒绝 |
| `malformed-attempt-success-with-reason.json` | StationAttempt | success 却带 reason_code → 拒绝 |
| `unknown-contract-version.json` | InstrumentObservation | contract_version "0.2" → 拒绝 |

## 语义边界

- `stn-002` 的仪器 `ast-delam-002` 在本夹具集中**没有读数**：缺失读数保持显式缺失，
  不用演示数据补齐；其可追溯表达是 `attempt-skipped.json` 与
  `run-outcome-completed-with-exceptions.json`。
- `marker_proxy` 只是以基准标记为代理的检测器，不等于通用图像识别；
  `unmatched` / `needs_review` 不构成已确认的客户设备 ID。
- 夹具中的资产 ID（`ast-delam-*`）是合成登记 ID，不对应任何真实设备台账。
