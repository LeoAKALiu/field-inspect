# 检查站采集

既有 route_orchestrator 在 FollowPath 到站后等待实测停止。station_pose_topic 默认 /Odometry（必须是路线 frame 的完整位姿和实测速度），station_image_topic 默认 /camera/image_raw。速度上限 0.02 m/s、0.02 rad/s，连续稳定 2 秒，位移 0.03 m、姿态 0.02 rad、位置方差 0.04 m² 门限；这些是待现场确认的开发配置，不是标定结果。

每次尝试在当前运行 artifacts/station-captures 下独立持久化 started、captured/failed 元数据与 PNG、源帧时间。采集成功且文件 fsync 后，route/complete_station 才可确认并继续。30 秒超时后停留在站点，route/retry_capture 只重新采集，不能自行驱动车辆；route/skip_station 保留带例外结果。

UI/服务端对旧站点索引的自动导入和 v0.3 station_attempt 转换尚需继续对接。现有源码与本次新增测试均未执行；站点动作未在车辆上运行。
