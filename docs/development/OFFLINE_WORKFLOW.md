# 离线巡检流程实施与交接

覆盖 [#5](https://github.com/LeoAKALiu/field-inspect/issues/5)—[#10](https://github.com/LeoAKALiu/field-inspect/issues/10)。当前为静态审查及实现准备；未运行测试、业务服务或车辆。前置票的运行验收尚未完成，本文的操作步骤均待实际执行，不构成正式交付或验收记录。

## #5 正式 v2 包导入

复用 `app.cli register-scene`、`import-package`、导入账本与任务回放。改动范围是共享站点索引校验及 CLI 回归用例；导入、崩溃恢复和显式重建索引都调用同一 `station_import.prepare`。不修改 v2 契约、旧包标签、归档原件或现场验收状态。包内存在站点采集却没有站点索引，或采集的 run/execution/station 没有对应尝试时，拒绝整个导入，不静默丢弃采集证据。

Mac 操作终端须与启动器使用同一组持久路径，见 [Mac 运行说明](MAC_RUNTIME.md)。使用实际已审批的场景登记 JSON 和完整离线包，以下占位值必须替换后执行：

```bash
export FIELD_RUNTIME_ROOT="$HOME/Library/Application Support/FieldInspect"
export TWIN_DB_PATH="$FIELD_RUNTIME_ROOT/twin.db"
export TWIN_IMPORT_ROOT="$FIELD_RUNTIME_ROOT/imports"
mkdir -p "$TWIN_IMPORT_ROOT/inbox"
# 确认同名目标不存在后复制完整目录；保留 U 盘原件，核对复制前后全部文件摘要。
cp -a /Volumes/INSPECTION/inspection-run-RUN.bag "$TWIN_IMPORT_ROOT/inbox/"
cd services/api
.venv/bin/python -m app.cli register-scene --file /ABSOLUTE/PRIVATE/registration.json --operator-label OPERATOR
.venv/bin/python -m app.cli import-package inspection-run-RUN.bag
```

记录命令退出码与 JSON、运行 ID、包摘要、代码/输入版本及数据路径，避免保存令牌。`imported` 为新导入，`duplicate` 为相同包，均不自动验收；`run_id_conflict` 不覆盖已有运行。失败保留原包和拒绝记录，修复真实来源后用新副本重试，禁止手改摘要或把真实包改标合成。`invalid_station_evidence` 需核对站点尝试、执行身份和采集索引；缺失正式证据应回到采集/导出端解决。

网页在任务回放中选择“现场实录”及准确运行，核对 run ID、`scene_version_id`、`alignment_id`、异常完成状态和待验收状态；场景请求必须携带运行原版本。检查缺失/篡改、场景不符、采集关联、路径/符号链接、受保护访问与失败重试时，用隔离数据库/归档及准确标记的测试输入，保留前后原件摘要。既有 CLI、v2 导入、访问与新采集关联用例均保留，当前未执行；#2 未验收，#5 保持 open。

## #6 站点图片处理

复用站点详情的“派生标记观测”和运行批次入口，见 [处理说明](STATION_PROCESSING.md) 与 [米制输入](METRIC_LOCALIZATION.md)。单张入口为 `POST /api/instruments/stations/{attempt_id}/captures/{sequence}/process`；`sequence` 是图像采集序号。批次使用同一处理器，冻结成功采集清单；暂停后等待当前项结束。界面批次完成不代表每项均获得有效位置。

查看原图、时间、图片/source/内参/检测配置摘要及报告 ID；新报告即使 blocked 或内参未核验，也保存可用必需输入的摘要。已有不可变报告不补写或重算。定位策略、实测尺寸与误差、外参、对齐和完整位姿/协方差缺项时，二维检测可保留，三维不补位；无检测、歧义或条件误差超限保持具体原因。仪器代理标记与验收地标分开布设；“待复核位置”“设备身份确认”“现场验收”分别处理。

新增准确标记的合成图片→v2 导入→实际处理 API→持久报告/无位置查询用例，覆盖检测、无匹配标记、未核验内参与缺检测配置，以及幂等/原件保留。与现有输入协方差用例一起保留但未执行，不将预填投影或语法编译当作检测/定位链通过。#5 未验收，#6 保持 open；真实米制处理仍需真实输入与运行证据。

## #7 点云与轨迹回放

保留 [有界点云预览](POINTCLOUD_PREVIEW.md) 的来源摘要、PCD 分片顺序、50 万点/大小限制、全局抽样和单位坐标变换；原件不改写，不借用其他场景点云。当前运行的原始轨迹、绘图轨迹与绑定场景在首次切换渲染即隐藏旧结果，同时取消网络请求/轨迹后续分页；迟到响应不得进入当前运行。既有点云 hook 取消请求并释放 Blob URL，查看器销毁后释放异步载入的几何/材质。

待授权后的浏览器检查应进入已导入运行，记录原场景版本、点云来源份数与展示/来源点数，核对真实引擎中的坐标与完整轨迹；再切换运行/场景并覆盖缺失点云、摘要/格式异常及超限输入，核对页面原因和资源释放。点云为整次运行的静态叠加，接口成功或出现画布均不算展示验收。新增首次渲染/取消/迟到响应/错误场景身份用例，既有 PCD/PLY 预览和引擎用例均保留未执行。#5 未验收，#7 保持 open。

## #8 点选同次采集证据

从本页定位列表或黄色定位符号选择记录，回放暂停；轨迹覆盖时跳到采集时刻，不覆盖时明确显示“未跳转”。详情展示采集时间、位置状态、图像摘要及该记录指定报告，额外核对报告的采集序号与唯一成功采集、图片摘要一致；冲突时停止展示该证据。切换运行/尝试/报告会在加载新详情前重置图片、分页及请求状态。

定位图层只显示当前页的有限有效位置，最多 25 条处理记录；无位置条目留在列表并展示原因。翻页/刷新/切换运行或场景时取消旧查询，当前列表和图层同时替换，不使用台账或车辆坐标。已有定位记录、报告来源验证、点选及资源释放用例保留；新增指定报告图片关联、切换清空和错误摘要拒绝用例，均未执行。操作时核对原图、标靶 ID、采集时间、处理报告与当前位置属于同一运行；不自动做资产匹配、确认或验收。#6 未验收，#8 保持 open。

## #9 现场短路线与真实正式包（待现场）

本节是待执行步骤，未开始采集。先完成 #3 的 [真实输入与部署核对](CAPTURE_READINESS.md) 和 #4 的 [坐标/停稳链验收](COORDINATE_CONSISTENCY.md)，由现场人员记录实际代码提交/脏状态、安装模块、配置摘要、输出盘挂载/可用字节与原门槛，以及标靶实测尺寸/误差、场景版本/对齐和同步/速度/误差预算依据。任一项不足即保留缺项，不降低门槛、不删除原数据、不覆盖脏工作区。

1. 在用户现场配合及现场运行安排确认后布置短路线和 2—3 个仪器代理标靶站点；正式验收地标另须覆盖 beginning/middle/end 三段，不能把仪器代理标记当作验收地标。
2. 同一定位会话内完成示教与执行；定位重启后重新示教并复核对齐。示教导出入口为 `inspection_route_teach TEACHING_RUN_DIR TEACHING_SPEC OUTPUT_DIR`；配置与示教录包必须来自本次会话，不使用示例代填实际站点或批准速度。
3. 现场操作员使用已核对的车端入口启停录包及路线。平台只回放证据，不控制车辆。按定位健康、独立底盘实测停稳及稳定驻留门槛采集，障碍停车后由人工确认继续。失败时保留原尝试，显式重试或跳站；中断后保留前段记录和实际运行状态，不补写成功。
4. 运行结束并落盘后，记录 run ID、原始 MCAP/轨迹/点云、每次采集状态、原截图/source、运行配置和场景输入。核对所有派生产物的来源提交、坐标、父子 frame 与摘要。
5. 使用下面既有导出入口完成核对，任一步失败即停止正式交接。`RUN_DIR`、`USB_ROOT`、`SCENE`、`ALIGNMENT` 必须事先绑定本次已核对的实际值；禁止用于合成输入。已有产物不覆盖，纠正来源时保留原件并使用新的私有运行副本。

```bash
# 已结束运行；下面操作只由现场人员在条件满足后执行，本轮未执行。
ros2 run inspection_pipeline inspection_trajectory_export "$RUN_DIR" "$RUN_DIR/config/scene_alignment.yaml"
ros2 run inspection_pipeline inspection_pointcloud_export "$RUN_DIR" "$RUN_DIR/config/scene_alignment.yaml"
ros2 run inspection_pipeline inspection_station_attempts_export "$RUN_DIR"
ros2 run inspection_pipeline inspection_acceptance_export "$RUN_DIR"
ros2 run inspection_pipeline inspection_run_validate "$RUN_DIR" --require-station-attempts
ros2 run inspection_pipeline inspection_bundle_export "$RUN_DIR" "$USB_ROOT" \
  --scene-id "$SCENE" --alignment-id "$ALIGNMENT" --require-station-attempts
ros2 run inspection_pipeline inspection_usb_finalize "$USB_ROOT" --unmount
```

正式输入和验收导出另见 [v2 包说明](PACKAGE_V2.md)。只允许真实 `completed` 或 `completed_with_exceptions` 状态的完整 v2 包；缺必需证据停止交付。不完整运行仅保留诊断产物，平台仍拒绝诊断包，不能改标合成绕过。记录导出及复制前后摘要、U 盘身份/卸载结果；保留 Jetson 原始运行、U 盘包和私有现场结果。导出成功不等于 Mac 回放或正式验收通过。#3/#4 前置验收和本次现场条件未满足，#9 保持 open。

## #10 完整真实案例与版本冻结（待真实案例）

先完成 #7/#8 软件验收和 #9 真实包，依次执行本页 #5 导入、#6 实际图片处理、#7/#8 三维点云/轨迹/定位及同次证据核对。原始真实图片必须从处理入口生成报告再回放，预填数据库和合成测试不能替代。记录同一 run ID、包/原件摘要、Scene Version/Alignment、处理器/OpenCV 版本、代码提交、配置/锁文件摘要以及软件/浏览器实际结果。

每次实际操作记录开始/结束时间、退出码/报告 ID、成功或失败原因及实际耗时：U 盘复制/摘要、场景登记/导入、单张/批次处理、点云加载、证据讲解。未发生的步骤填写“未执行”，不估造耗时。依据完整排演结果决定现场新包是否加入讲解；保留“已有真实案例→现场短路线→交流”的顺序。现场失败或新包未处理完时回到已完成的真实案例；目前没有经贯通确认的真实案例，不能用演示素材充当后备案例。

独立备份在停止所有 API/CLI 写入、暂停批次并等待当前项结束后制作，数据库应使用 SQLite 一致快照，或在完全停写后保留数据库及仍存在的 WAL/SHM；不复制正在变化的数据库主文件冒充完整备份。保存原始 Jetson 运行、原包、完整导入归档、数据库、私有标定/配置/登记依据和匹配的源码/锁文件/构建产物。私有凭据独立按原权限保存，不进入公开 PR 或云盘源码交接。

既有 `verify-backup` 要求另一块磁盘上的每个案例目录包含 `archive/`（该 run 的完整归档副本）、`twin.db`（整个账本一致快照）、`scene-version.sha256`、`alignment.sha256`，后两者为对应登记原件的摘要文本。实际资产和原始依据也须随私有备份保存，摘要文本不能替代原件。核对文件集合及逐文件摘要后，从同一持久路径环境运行：

```bash
cd services/api
.venv/bin/python -m app.cli verify-backup RUN_ID --backup-root /Volumes/INDEPENDENT_BACKUP/CASE
```

同盘副本/未实际完成的云同步不算独立备份。恢复先在新的隔离私有数据目录中使用相同代码/依赖版本，保持原件；不要覆盖当前库或让旧代码直接打开已升级库。完整恢复能力需要实际启动、查询及三维回放检查；本轮未执行。导入、处理、展示与备份成功均不自动改变验收，保留 inbox/U 盘原件，不为冻结执行清理或 `accept-run`。

只有完整案例、关键软件结果、独立备份/恢复与演示顺序核对均有实际记录后，才记录冻结提交、锁文件/配置/案例/构建摘要、操作记录位置和冻结时间；之后更改输入或代码须重新核对。当前仅保存开发候选提交和交接检查点，不创建正式版本标签或就绪声明。

| 状态类别 | 当前证据 | 下一完成条件 |
| --- | --- | --- |
| 软件实现/静态检查 | #5—#8 修补，类型/语法/静态检查；实际结果见阶段 PROGRESS | 授权后的 CLI/API/浏览器及 ROS 入口验证 |
| 完整真实案例回放 | 未贯通；未记录真实处理/展示耗时 | 真实包从导入、处理到同运行三维和原图/报告核对 |
| 现场检查 | #3 未就绪，#4 未运行验收；#9 未现场执行 | 真实输入、部署/存储、用户配合和现场记录 |
| 正式验收 | 未执行；保持原独立状态 | 依据正式证据进行独立验收，不由演示推导 |
| 独立备份/冻结 | 仅源码阶段交接，未制作/恢复真实案例备份 | 不同磁盘的完整备份、恢复核对及演示版本冻结 |

#7/#8/#9 尚未验收，#10 保持 open。公开 PR 仅包含审核过的技术源码、准确标记的合成用例和操作说明，不包含原私有历史、运行数据或未核对授权资产。
