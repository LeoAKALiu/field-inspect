# 站点标记米制定位

现有站点 process API 升级为 marker-proxy-metric-v2：先保留二维观测，再尝试米制定位。处理器版本进入报告身份，新结果不覆盖旧版二维报告。所有结果仍随报告在同一事务保存，residual_m 保持 null，估计位置使用 needs_review，不自动确认设备身份。

## 冻结输入

config/instrument-localization.json 的严格模型是 services/api/app/marker_localization.py 中的 LocalizationPolicy；由该模型导出的 JSON Schema 位于 packages/contracts/instrument-localization.schema.json。未知字段被拒绝，所有误差和门槛都必须由实际测量/政策明确提供，没有假标定默认值。

配置必需包含：

- schema_version="1.0"、source_reference：测量及误差预算依据。
- scene_version_id、alignment_id，以及 alignment_evidence_sha256、camera_calibration_sha256、extrinsic_sha256、scene_alignment_sha256，分别精确绑定运行注册场景、对齐证据和三个冻结输入。
- markers：用十进制标记 ID 字符串索引；每项 physical_size_m 为实际印刷黑边外沿边长，size_sigma_m 为尺寸测量标准差。
- image_model_sigma_px：在“相机内参/畸变参数视为精确”的条件模型中，每个角点坐标的独立标准差；不能用重投影 RMSE 自动替代。
- max_pose_gap_seconds（大于 0、不超过 0.5）、max_reprojection_rmse_px、ambiguity_margin_px、max_position_std_m、max_timing_displacement_m。
- max_linear_speed_mps、max_angular_speed_radps：图像到位姿时刻区间内的速度上界；单帧测得速度不能证明整个区间上界。
- uncertainty：extrinsic_position_sigma_m、extrinsic_rotation_sigma_rad、alignment_position_sigma_m、alignment_rotation_sigma_rad。采用独立、各向同性、小角度误差模型，各项必须为正数。

继续使用既有 alignment/camera_to_scene.json 的 base_from_camera、base_frame、alignment_id、scene_alignment_sha256，并要求 camera_frame 与图像 frame 一致。config/scene_alignment.yaml 的 transform.translation_m / rotation_xyzw 表示 scene_from_map，input_frame 须匹配 odometry 父 frame，输出必须为 scene_local_yup。两者均要求 verified 声明；它不是本阶段重新完成现场验证的证明。

source.json 1.2 新增 pose.covariance_6x6，保留 ROS 36 个协方差元素，顺序为父坐标系下 xyz 与固定轴旋转 xyz。必须有限、对称、半正定且非全零。1.1 旧包缺少完整协方差时阻止定位，不将三项位置方差扩充为虚构的六自由度协方差。图像/位姿时间须在运行内，时间差须与记录一致且不超限。

## 求解与拒绝规则

遵循 [OpenCV solvePnPGeneric / IPPE_SQUARE 契约](https://docs.opencv.org/4.5.1/d9/d0c/group__calib3d.html)：按标记左上、右上、右下、左下角顺序建立边长为米的正方形对象点，保留返回的姿态解并独立计算角点像素重投影 RMSE。拒绝负深度、无有限解、RMSE 超限、前两个候选误差差值不大于 ambiguity_margin_px，以及退化雅可比。

位置链为 scene_from_map × map_from_base × base_from_camera × marker_center_camera。标记中心是代理标记位置，不保证与设备物理中心重合；本阶段不输出仪器朝向。歧义、误差或输入缺口会保留在报告中，不输出单一位置。

## 误差预算的含义

使用 projectPoints 对 rvec/tvec 的雅可比伪逆，在指定 image_model_sigma_px 下得到条件姿态协方差，再传播尺寸、外参、完整 odometry 协方差和场景对齐误差。报告输出 scene_local_yup 下 conditional_position_covariance_m2、最大特征值平方根 conditional_max_position_std_m。

同步另列确定性边界：gap_seconds × (max_linear_speed_mps + max_angular_speed_radps × 标记到车体原点距离)，记录为 timing_displacement_bound_m。它没有与高斯标准差混成单一“置信度”。两项各自与策略门槛比较，超限时不输出位置。

这些是条件模型估算，不是实测位置残差、95% 覆盖率或精度保证。内参/畸变不确定性、跨来源相关性、非高斯误差、机械形变和违反速度上界等未被该模型证明覆盖，仍需实际标定、误差预算复核与现场验收。confidence 仍保持保守 0，residual_m=null，既有自动资产匹配不会据此确认设备。

## 本阶段验证范围

仅静态阅读、依赖安装、Schema 生成和 Python/TypeScript/Vite 编译。未执行求解器、图片检测、业务接口、单元、集成、E2E、性能或实车测试。不能据此宣称精度达标。
