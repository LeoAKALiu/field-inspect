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

FastAPI /openapi.json 是包含新接口的运行时 API 定义；旧 packages/contracts/openapi.yaml 尚需同步，不作为新接口全部覆盖的声明。批量历史导入按行原子，映射版本先持久化；读取只使用明示映射，不读取客户数据库备份。阈值为同单位上限规则，无法表达更复杂工程规则时不得套用默认值。读取接口目前未分页，规模化需要后续优化。

匹配接口消费已有代理检测与定位结果；它不是通用仪器视觉模型。检测与定位数据的现场真实性仍由来源核验和人工复核保证。新服务没有独立车辆控制入口。
