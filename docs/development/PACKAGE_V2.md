# 正式包 v2 导出

内部运行 manifest、轨迹和点云 provenance 保留各自 v1 版本；交接 manifest 与交接轨迹使用 v2。正式导出使用共用的 packages/contracts/python/astra_inspect_contract 验证器，与服务器入口相同。原始轨迹字节保存在包内 metadata/source-trajectory-v1.json。所有 MCAP 分片均在 inventory 中，bag.path 引用第一片；其余分片由原 rosbag metadata.yaml 保持顺序。

## 现场输入

在已结束运行目录中提供以下经审查的真实文件（不在源码库中保存现场文件）：

- config/package-v2.json：只有 scene_version（scene_id、scene_version_id、asset_sha256）与 alignment（alignment_id、evidence_sha256）。摘要分别绑定下面 scene-version.json 与 evidence.json 的原始字节，须与平台登记一致。
- scene/scene-version.json：场景 ID、版本 ID、scene_local_yup；landmarks 列表包含 landmark_id、marker_id、dictionary、role、physical_size_m、registered_pose、pose_tolerance、min_valid_samples；required 另含 route_portion。字段含义见共享 v2 schema。
- alignment/evidence.json：已登记对齐证据，包含 alignment_id 与 scene_version_id。
- config/scene_alignment.yaml：沿用现有经测量审批的对齐格式。
- config/camera_calibration.json：verified、width、height、3x3 camera_matrix、distortion_coefficients。参数必须来自真实相机标定。
- alignment/camera_to_scene.json：verified、transform_id、alignment_id、scene_alignment_sha256、base_frame、camera_frame、base_from_camera（4x4 刚体矩阵，把光学相机坐标变到里程计 child_frame）。scene_alignment_sha256 绑定上面的 YAML；每帧通过实录位姿插值得到 camera→scene，不使用一个静态相机位姿代替移动车辆。
- config/acceptance-export.json：image_topic、pose_topic、dictionary、detector_parameters 对象、max_pose_gap_seconds。显式填写 ROS topic 和 ArUco 字典，不能用 AprilTag 代理配置替代。

在 ROS 目标机运行 `ros2 run inspection_pipeline inspection_acceptance_export RUN_DIR`。逐帧处理整个录包，保留所有有效登记地标检测，首个有效检测只用于代表图。位姿没有时间覆盖、帧身份不符或标定缺失即停止，输出只在完成后原子出现。记录未知标记和 PnP 失败计数。已存在输出不覆盖；纠正来源时使用新的私有运行副本。

随后按既有命令运行 `inspection_bundle_export RUN_DIR USB_ROOT --scene-id SCENE --alignment-id ALIGNMENT`。正式完成状态必须通过 v2；不完整运行仅允许显式 --allow-incomplete 导出诊断包，平台继续拒绝诊断包。

验收图像不添加为公共展示资产，保持服务器本机审阅边界。最少样本、地标残差和 required 地标覆盖仍需平台现场验收，不把包通过当作验收通过。

ROS 部署须在目标 Python 中安装 bagit==1.9.0、pydantic>=2.10,<3 以及支持 aruco 的 OpenCV；colcon 包装安装共享 Python 模块与同一份 schema。不能使用系统 pydantic v1 代替。

新契约锁绑定单仓库 v2 schema；此前按旧锁生成的点云应使用原始 MCAP 在新工作区重新派生，不修改其旧来源摘要。测试全部未执行，真实标定和录包仍待现场提供。
