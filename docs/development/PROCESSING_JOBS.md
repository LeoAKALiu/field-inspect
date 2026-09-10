# 可恢复的站点批量处理

## 操作接口

- POST /api/instruments/runs/{run_id}/jobs：在同一事务中冻结已索引成功采集清单及处理器版本。最多 10000 项；同一清单和版本返回同一批次，不重复创建。
- GET /api/instruments/jobs?run_id=...&cursor=...&limit=...：批次分页。
- GET /api/instruments/jobs/{job_id}：状态和逐项计数。
- GET /api/instruments/jobs/{job_id}/items?cursor=0&limit=50：明细分页。
- GET /api/instruments/jobs/{job_id}/events?cursor=0&limit=50：追加式事件历史。
- POST /api/instruments/jobs/{job_id}/pause、resume、retry_failed：暂停、恢复、仅重试失败项。

所有接口沿用现有全局访问控制。pause 不打断正在持有归档写锁的当前项；当前项结束后不领取下一项。输入缺口 blocked 是已生成的不可变报告，不属于可通过原地重试修复的异常。批次 completed 表示处理结束，不表示定位、识别或验收通过。

## 持久性与单写者

processing_jobs、processing_job_items、processing_job_events 保存在同一 SQLite 数据库中，并纳入备份账本摘要。批次创建、领取与结果标记分别使用短事务；实际包校验和处理在事务外执行，以免长时间占用数据库连接锁。

API lifespan 启动一条后台线程，通过 import_root/.station-worker/.import-writer.lock 排他持有工作者身份。每项仍调用既有 station_processing.process，继续获取原归档写锁，校验整个归档并按报告 ID 幂等保存。工作者不会绕开包校验；因此大包的每项校验开销仍然存在，未做性能验证。

取得工作者锁后，将上次遗留 running 项重排为 queued，并保留恢复事件。若上次报告已经提交但队列未写结果，重跑会命中原报告，不覆盖或重复观测。原来 paused 的批次保持暂停。进程优雅退出等待当前项结束，再关闭数据库；强制中断由下一次启动恢复。

处理异常逐项保存，继续其他项；归档写锁忙时延后重排。retry_failed 只重排 failed 项，保留历史事件和尝试次数。处理器版本变化后旧批次暂停，须创建新版本批次，不能在旧批次中混用处理逻辑。

进行备份前暂停批次并等当前项结束，同时停止其他写入，再复制/验证账本；活跃队列状态变动会使备份摘要不一致。恢复备份后 queued/running 项会续跑，paused 项不会自动恢复。

## 列表分页

新增 GET /api/instruments/pages/{kind}?run_id=...&attempt_id=...&cursor=...&limit=...；limit 默认 50、范围 1—100，返回 items 和 next_cursor。记录按稳定 ID 递增游标读取，报告可按 attempt_id 筛选。GET /api/instruments/records/{kind}/{identity} 直接读取单条证据。

记录分页是实时视图，不是固定快照：游标之前新插入的记录需回首页刷新后查看；清单不会因 offset 漂移而重复。前端站点每页 25 条、报告每页 10 条、批次明细每页 25 条。站点只在当前页按时间展示，不声称全局时间排序。旧 /records/{kind} 数组接口保留兼容，尚未将所有旧业务页迁移到分页。

## 执行范围

本阶段只安装/编译和静态检查，没有创建或执行实际批次，没有执行故障注入、处理接口、单元、集成、E2E、性能或实车测试。恢复行为是代码实现，尚未通过断电/崩溃演练验证。
