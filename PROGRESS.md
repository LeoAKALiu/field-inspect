# Field Inspect 开发检查点

更新时间：2026-09-10，ProArt / Codex。本文件与当前源码同批提交，精确 HEAD 见 git rev-parse HEAD；iCloud 检查点另附实际提交号。

## 工作区与恢复

- 唯一开发工作区：C:/Projects/AstraInspect/product/astra-inspect，开发分支 main。
- 私有来源：C:/Projects/AstraInspect/sources；scout_mini_ws 分支 fix/static-recording-hardening，efb03c016ccf85d1f52e3541acac141070b01654；digital-twin 分支 feat/contract-v03-instrument-objects，eaf25fd36f2354a37ab6da6281a46fd04f956dd0。基线未修改。
- iCloud 原始交接已完整读取：15 项 SHA256、449 个跟踪文件和 33 份上下文字节全部一致；Git fsck 无报错。恢复说明被 iCloud 命名为 (1)，实际哈希一致。原交接副本及报告在工作根 private。
- 工具链：Windows x86 Git 2.55、Node 22.23.2、pnpm 9.15.0（通过 npx 指定）、Python 3.12.10、uv 0.12.9；另有 Ubuntu 24.04 WSL。未产生 Jetson ARM64 部署产物。

## 已实现

- M0：平台根布局整合 edge/ros2，新历史、品牌和客户 README。阶段提交 02ffbe2、771ddd7。
- M1：共享 v2 语义验证、正式导出、场景/对齐绑定、inventory/包摘要、原始轨迹追溯、诊断包边界；MCAP 首末帧和完整 ArUco 观测派生入口。阶段提交 884a4cf。没有实际生成或导入真实巡检包。
- M2：实测速度与定位稳定门槛、站点图像持久化、显式重试、失败/超时记录和人工确认继续。阶段提交 d50aaf4。未运行车辆动作。
- M3/M4：沿用 v0.3 领域模型，SQLite 设备台账、观测、定位、候选匹配、追加人工复核；三通道原字段/单位/时区/映射版本、逐行历史导入、观测时刻关联、未来/失效排除、趋势和显式阈值建议。
- M5：真实 /instruments React 页面连接 API，可登记/导入/查看/导出 JSON；默认本机访问，代理/网络模式使用显式密钥和 8 小时 HttpOnly 会话；WebSocket 同步保护。备份摘要包含新增业务表。
- M6：prompts/FRONTEND_HANDOFF.md 已准备真实页面、接口和两阶段提示。未启动 Kimi/Gemini、其他 agent 或并行 worktree。
- M7：客户操作指南、开发入口、来源许可与公开筛选。实机序列号/外参已替换为未标定示例；私有源保留原配置。LIRIS 官方许可及两个资产哈希核对完成；两份补丁按锁定上游版本保存许可证。

## 编译、启动与测试

- TypeScript/Vite 和 API Python 编译完成；Vite 提示部分块大于 500kB，未做性能验证。
- 增补 Windows 文件锁后，API 日志显示 Application startup complete；网页预览进程已启动。未执行 API 请求、交互验收或功能探测。
- 本机网页 http://127.0.0.1:4173；API http://127.0.0.1:8000；日志、数据库、运行记录在 C:/Projects/AstraInspect/private/runtime。
- 全部单元、集成、E2E、性能、故障注入和实车测试：未执行。测试文件保留。编译和启动不是测试通过。
- 平台 uv 锁保留原版本和 PyPI 来源，仅新增 Windows 时区数据库 tzdata。构建改用 --frozen --no-dev，避免隐式改锁/装测试依赖。Livox 构建脚本中的日志探测改为显式选择，默认仅编译。

## 发布边界

公开仓库：https://github.com/LeoAKALiu/field-inspect（已创建，干净快照发布由本阶段完成）。私有开发 main 包含筛选前的配置，不得推送！只推 public-main 的审核快照到远端 main，首次提交无父历史，后续公开快照只以已公开提交为父节点。

保留 UNLICENSED 和各子包现有许可，没有擅自为整个产品声明 MIT。bundle、源仓库 Git 历史、客户数据、私有标定和内部资料不发布。

## 尚未完成与下一条操作

1. 下一条具体操作：为 station_processing 的已实现单次派生增加可恢复的批量处理调度，并为 processing_report/站点证据列表增加分页；保持已处理记录不可变，失败逐项记录，不重建仓库。
2. 已实现单次二维派生、条件米制估计和证据详情；批量调度、列表分页、静态 openapi.yaml 同步仍需实现。三维设备选择与历史读数面板尚未做交互验收。
3. 多用户身份与细分权限尚未完成，当前仅共享操作密钥，自报标签不是已认证个人。
4. 客户数据库尚未访问；真实映射、单位、阈值需私有审计。通用无标记识别缺训练数据；现场标定、PTP、完整路线与长时采集需另行输入/验收。
5. M0—M7 不是全部完成；以上新增功能全部尚未测试。前端优化仅准备交接，未调用外部模型。

## 仓库更名

按用户选择，GitHub 仓库改为 LeoAKALiu/field-inspect，项目显示名称改为 Field Inspect；本地 public 远端同步更新。原本地工作路径继续有效。历史数据的 producer、内部 Python 包名和已有环境变量保留兼容；本轮未执行测试。


## 站点原子索引与三维历史证据（本阶段）

- v2 导入在任务/轨迹/账本同一事务内写 StationAttempt 和 station_evidence；核对站点索引、源摘要、UUID 身份、运行时间范围、采集状态与图片摘要，冲突回滚。
- 保留路线执行起点的 MCAP 区间语义、pending_confirmation 和独立 capture_attempt_seq，不把路线完成当成图像检测完成。
- 旧归档通过受保护的 POST /api/instruments/runs/{run_id}/reindex-stations 显式幂等补索引，先校验归档完整性和原导入摘要；保留原任务与轨迹。
- 回放页列出站点结果并跳转到真实轨迹时间；同版本台账设备进入三维，点击暂停并按选择时刻关联三通道历史读数。请求取消和运行切换隔离避免显示上一运行数据。
- 客户操作指南和站点开发文档已更新。图片仍在本地归档，未新增图片下载接口；无索引旧包不补造记录。
- TypeScript/Vite 与 Python 编译完成；全部单元、集成、E2E、性能、故障注入、实车测试未执行。没有实际导入/重索引真实包，没有调用业务 API 验收。已重启本项目本地 API/预览进程加载本次实现；只检查启动日志，没有执行接口请求或交互测试。


## 采集详情与服务端二维标记观测（本阶段）

- 新增受保护的单次采集 process API；校验归档、账本摘要、输入哈希、真实相机标定声明、图像尺寸与 frame、标记字典和 ID，拒绝复用验收地标。
- 观测、未定位记录、处理报告在同一事务持久化，保留原始角点和处理器版本。输入缺失保存 blocked；无配置标记不造观测。confidence=0 是无概率标定时的保守值，不冒充检测器概率；无可靠 3D 输入不输出位置。
- StationEvidenceDetails 展示每次采集状态/原因/哈希，可派生观测并查看具体缺口。图片仍留本地归档。
- 车端 source.json 1.1 新增完整最新测量位姿、父子 frame、方差和图像/位姿时间差；不是插值或场景定位结果。旧包不会追补假位姿。
- 锁定 OpenCV headless 4.11.0.86 与 NumPy；显式 PyPI 来源，原有锁定包版本保留；复制依赖发行包许可原文。新增 docs/development/STATION_PROCESSING.md。
- 前端、API、车端 Python 编译完成。单元、集成、E2E、性能、故障注入、实车测试全部未执行；没有实际图片检测或调用处理接口。
- 真实尺寸/外参/同步与误差求解尚未完成，M0—M7 未全完成。后续按上方下一条操作续接。

- 本阶段本地 API/预览已重启并读取启动日志；未请求业务接口。Vite 仍提示大资源块，未做性能测试。


## 条件米制定位（本阶段）

- marker-proxy-metric-v2 接入 IPPE 方形标记求解，保留多解重投影误差，拒绝歧义、负深度、超限与退化解。
- 新增 LocalizationPolicy 严格输入模型及导出 JSON Schema，绑定场景/对齐/标定哈希、坐标系、完整位姿与时间差；沿用原场景对齐质量门槛。
- 传播角点条件误差、尺寸误差、外参、完整 ROS pose 协方差与场景变换误差；条件最大轴标准差和同步位移上界分开保留。内参与畸变视为精确、独立小扰动等假设写入文档；不宣称实测精度。
- 输出位置保持 needs_review，heading_deg/residual_m 为空、confidence=0，不自动确认资产。旧版报告保留；缺少输入仍保存二维观测与明确阻塞原因。
- 车端 source.json 1.2 保存全部 36 个协方差元素并拒绝非有限协方差进入稳定采集；不为旧档案补造缺失项。
- 详情页展示场景坐标、候选像素 RMSE、条件标准差和同步上界。文档见 docs/development/METRIC_LOCALIZATION.md。
- Python、TypeScript/Vite 编译与 Schema 生成完成；未执行求解器、图片检测、任何业务接口或单元/集成/E2E/性能/实车测试。Vite 大块提示仍在，未做性能验证。
- 真实标记尺寸、现场标定、速度边界与误差预算复核依赖现场输入；M0—M7 未全完成。

- 本阶段本地 API/预览已重启，启动日志正常；未请求业务接口。
