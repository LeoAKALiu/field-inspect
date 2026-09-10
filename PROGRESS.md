# Astra Inspect 开发检查点

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

公开仓库：https://github.com/LeoAKALiu/astra-inspect（已创建，干净快照发布由本阶段完成）。私有开发 main 包含筛选前的配置，不得推送！只推 public-main 的审核快照到远端 main，首次提交无父历史，后续公开快照只以已公开提交为父节点。

保留 UNLICENSED 和各子包现有许可，没有擅自为整个产品声明 MIT。bundle、源仓库 Git 历史、客户数据、私有标定和内部资料不发布。

## 尚未完成与下一条操作

1. 下一条具体操作：阅读 services/api/app/run_bundle.py 与 edge/ros2/src/inspection_pipeline/inspection_pipeline/station_attempts_export.py，将归档的站点索引/采集档案在同一原子导入中转换为平台 v0.3 StationAttempt，并把证据关联接到真实回放页。不要重建仓库。
2. 观测检测结果的自动处理、三维目标选择与新设备面板联动、列表分页、静态 openapi.yaml 同步仍需实现。
3. 多用户身份与细分权限尚未完成，当前仅共享操作密钥，自报标签不是已认证个人。
4. 客户数据库尚未访问；真实映射、单位、阈值需私有审计。通用无标记识别缺训练数据；现场标定、PTP、完整路线与长时采集需另行输入/验收。
5. M0—M7 不是全部完成；以上新增功能全部尚未测试。前端优化仅准备交接，未调用外部模型。
