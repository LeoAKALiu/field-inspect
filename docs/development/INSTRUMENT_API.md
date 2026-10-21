# 设备业务 API

沿用现有 SQLite 单进程服务和 v0.3 InstrumentObservation、InstrumentLocalization、AssetMatch、InstrumentReading、StationAttempt。新代码位于 services/api/app/instruments.py 和 routers/instruments.py；数据与旧模拟设备曲线分开，演示记录不能附加到实录运行。

| 接口 | 行为 |
|---|---|
| GET/POST /api/instruments/assets | 已登记场景下不可变设备台账 |
| GET/POST /api/instruments/records/{kind} | 观测、定位、站点尝试；引用校验与幂等 |
| POST /api/instruments/matches | 显式标记族/ID + 位置 + 置信度候选匹配 |
| POST /api/instruments/reviews | 追加人工复核，不覆盖匹配历史 |
| POST /api/instruments/readings | 三通道及原字段、时区、映射版本 |
| POST /api/instruments/history | 显式映射的逐行历史导入，返回保存/拒收明细 |
| GET /api/instruments/assets/{id}/analysis?at=... | 时间之前有效读数、排除原因和规则建议 |
| GET /api/instruments/observations/{id}/analysis | 观测时刻关联，使用最新复核结果 |
| GET /api/instruments/runs/{run_id}/marker-localizations?cursor=...&limit=25 | 本实录运行的标靶定位分页投影，核对处理报告、采集和包来源；保留无位置原因 |

FastAPI /openapi.json 是运行时 API 定义，packages/contracts/openapi.yaml 由实际路由的无数据库模式同步生成。业务页使用分页入口；旧数组读取接口保留兼容。批量历史导入按行原子，映射版本先持久化；读取只使用明示映射，不读取客户数据库备份。阈值为同单位上限规则，无法表达更复杂工程规则时不得套用默认值。

标靶定位投影只连接现有 marker-proxy-metric-v2 的观测、定位与处理报告，返回原定位对象，不在读取时执行检测/求解或确认资产。单页默认 25 条、上限 100 条，按定位 ID 游标前进；手工记录及无关联报告的旧结果保留原读取入口。来源冲突返回 422，不返回部分可疑点位。具体来源核对与三维行为见 [观测定位展示](LOCALIZATION_VIEW.md)。

匹配接口消费已有代理检测与定位结果；它不是通用仪器视觉模型。检测与定位数据的现场真实性仍由来源核验和人工复核保证。新服务没有独立车辆控制入口。
