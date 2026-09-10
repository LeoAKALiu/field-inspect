# 站点观测派生

POST `/api/instruments/stations/{attempt_id}/captures/{sequence}/process` 只读取导入账本对应的不可变归档。先取得导入写锁，校验 BagIt、v2 语义和原始包摘要，然后核对选中的 captured 记录。传入的 sequence 是采集重试序号，不是路线重试序号。服务端不接收任意文件路径。

## 输入

归档 inventory 中必须包含：

- 同一采集目录的 frame.png 与 source.json；图片不超过 32 MiB、2000 万像素。
- config/camera_calibration.json：verified 为 true，camera_matrix 为 3×3 或九元素数组，distortion_coefficients 为 4/5/8/12/14 元素数组，image_width/image_height 与实际图片一致，frame_id 与采集帧一致。verified 声明不能替代现场标定与验收。
- config/instrument-detector.json：schema_version 为 "1.0"，dictionary 为明确的 OpenCV ArUco 字典名称，marker_ids 是非空、唯一的仪器代理标记 ID 列表。必须来自实际布设，不得复用该场景的验收地标。

配置冻结在运行快照中，经车端导出纳入 inventory。不要修改已归档包来补输入：保留原包、修正采集配置并导出新的运行。相同包、站点、采集序号与处理器版本的结果幂等；升级处理算法或 OpenCV 版本时必须提升 VERSION。

## 输出和边界

原始检测角点、字典、标记 ID、输入摘要、OpenCV 版本保存在 processing_report，可用 GET /api/instruments/records/processing_report?run_id=... 查询。观测、未定位记录和处理报告在同一事务提交，失败时整体回滚。

缺少输入时保存 blocked 报告；无匹配标记时保存 no_configured_marker_detected，不补造观测。识别成功时保存现有 v0.3 InstrumentObservation 和无位置的 InstrumentLocalization。没有经过校准的检测概率，因此 confidence 固定为保守的 0，避免被既有自动匹配门槛误认为高置信结果。它不是模型输出概率。状态保持待复核，不能用来宣称通用仪器识别或确定设备身份。

本阶段仅完成二维 marker_proxy 派生。米制定位仍需真实标记尺寸、相机外参、场景变换、帧与位姿同步校验，以及定位误差的明确计算方法。不可把像素重投影误差当作 residual_m。

车端 source.json 1.1 新增完整 odometry 位姿、父子 frame、位置方差、image_pose_gap_ns 和 latest_measured_odometry 方法名。它是与图像相邻的最新测量，不是插值位姿，也不是场景坐标下的定位结果。旧档案只有时间戳时准确报告 complete_capture_pose_missing。

## 运维

按锁文件安装服务端依赖；新增 opencv-python-headless 4.11.0.86 和 NumPy，不安装 GUI OpenCV。依赖来源显式固定 PyPI，保留原有依赖版本。前端站点列表中的“查看采集证据”可查看每次重试和处理报告；“派生标记观测”会读取整个归档校验完整性，耗时随包大小变化。

全部单元、集成、E2E、性能与实车测试未执行；也没有执行图片检测或处理接口。编译不等于功能验收。
