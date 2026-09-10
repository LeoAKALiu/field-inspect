# 检查站采集

既有 route_orchestrator 在 FollowPath 到站后等待实测停止。station_pose_topic 默认 /Odometry（必须是路线 frame 的完整位姿和实测速度），station_image_topic 默认 /camera/image_raw。速度上限 0.02 m/s、0.02 rad/s，连续稳定 2 秒，位移 0.03 m、姿态 0.02 rad、位置方差 0.04 m² 门限；这些是待现场确认的开发配置，不是标定结果。

每次尝试在当前运行 artifacts/station-captures 下独立持久化 started、captured/failed 元数据与 PNG、源帧时间。采集成功且文件 fsync 后，route/complete_station 才可确认并继续。30 秒超时后停留在站点，route/retry_capture 只重新采集，不能自行驱动车辆；route/skip_station 保留带例外结果。

平台导入和 v0.3 station_attempt 转换见下文；采集证据专用详情页仍需后续实现。测试未执行；站点动作未在车辆上运行。


## 平台归档索引与回放

正式 v2 包中的 artifacts/stations/attempts.json 在任务、轨迹、导入账本的同一事务内转换为 v0.3 StationAttempt；任一身份、源摘要、时间边界或不可变记录冲突均回滚。没有站点索引的旧包不生成记录。

同时保存 station_evidence，保留 execution_id、原始 MCAP 区间、档案哈希以及同一运行/执行/站点的采集记录和图片哈希。路线 attempt_seq 与 capture_attempt_seq 各自计数，不能混同。采集图片仍保留在本地归档，本次未增加图片下载接口。

MCAP 区间起点是路线执行开始，不是站点到达时间；success 是路线状态结果，不是检测成功。服务端校验包完整性、索引及结构，不独立重放 MCAP；记录保持 pending_confirmation。采集档案不存在时不补造图像成功。

旧归档使用 POST /api/instruments/runs/{run_id}/reindex-stations。该操作校验归档完整性、导入摘要后以事务幂等写入，不覆盖任务、轨迹或已有不同内容的记录。沿用全局访问控制与导入写锁。GET /api/instruments/records/station_evidence?run_id=... 可读关联元数据。

任务回放提供站点结果时间跳转，超出实际轨迹范围时禁止跳转；场景版本一致的登记设备显示在三维中。选择设备暂停播放，按选择时刻查询三通道读数；不会自动取当前最新值代替历史值。设备离线标志表示没有实时遥测连接。

本阶段未执行任何单元、集成、E2E、性能或实车测试。
