# 正式巡检包离线交接

SCOUT Mini 巡检期间完全离线。Digital Twin 不接收车辆实时遥测，不属于车辆安全链，
也不提供在线上传入口。一次巡检结束并完成本机落盘后，由操作员使用可移动介质把
完整 Formal Inspection Package 复制到服务器本地 inbox。

## 契约版本

- **v2 是唯一正式交接契约。** `schema_version: "2.0"` 且 `source_kind: "inspection_run"`
  的包才可进入生产导入边界。权威 schema 为
  `packages/contracts/inspection-package-v2.schema.json`，TypeScript 类型为
  `packages/contracts/src/inspection-package.ts`，Python 边界模型为
  `services/api/app/inspection_package.py`。
- **正式运行状态只允许 `completed` 和 `completed_with_exceptions`。** 后者必须带非空
  `exceptions`，不得被映射成普通 `completed`。
- **v1 不承担生产兼容义务。** 真实 v1 `inspection_run` 包被拒绝。v1 仅可用于明确标记的
  `source_kind=synthetic_contract_fixture` 开发或迁移测试
  （`packages/contracts/run-bundle.schema.json` 与
  `data/contract-fixtures/scout-run-bagit-v1/`）。

黄金包：

- `data/contract-fixtures/inspection-package-v2-completed/`
- `data/contract-fixtures/inspection-package-v2-completed-with-exceptions/`

可用 `uv run --project services/api python scripts/build_inspection_package_v2_fixtures.py`
重新生成。

## 包格式

传输目录遵循 RFC 8493 BagIt 1.0，必须自包含，**禁止 `fetch.txt`**：

```text
inspection-run-<run_id>.bag/
├── bagit.txt
├── bag-info.txt
├── manifest-sha256.txt
├── tagmanifest-sha256.txt
└── data/
    ├── manifest.json                 # v2 语义清单
    ├── config/system.yaml
    ├── replay/trajectory.json        # 完整 Replay Trajectory
    ├── bags/run.mcap                 # 必需引用，只归档不解析
    ├── acceptance/                   # 首帧、末帧、ArUco 标注图
    ├── alignment/                    # 对齐证据副本（可选，摘要必填）
    └── artifacts/                    # 可选展示产物（点云等）
```

`data/manifest.json` 绑定不可变 `run_id`、`package_sha256`（除 manifest 外的载荷清单
canonical SHA-256）、精确 Scene Version（`scene_id` + `scene_version_id` +
`asset_sha256`）、`alignment_id` 和对齐证据 SHA-256。路径必须是安全的 POSIX 相对路径。

完整 Replay Trajectory 使用 `scene_local_yup`、严格递增 UTC 时间和从 0 开始的连续序号，
并与运行、Scene Version、alignment 保持一致。

`acceptance_evidence` 包含首帧、末帧，以及每个 ArUco Landmark 的完整观测段、代表性标注图
和 Evidence Lineage（相机标定摘要、检测器及配置、MCAP 帧时间戳、角点、相机/场景位姿、
变换身份和双残差）。声明的样本数与双 95 分位必须与观测序列一致（线性插值，
index = (n-1)*p/100）。

## 明确不做

- **不解析 MCAP。** 只校验引用存在、非空，并随包归档。
- **不承诺点云渲染。** 点云和其他 Display Artifact 可选；缺失保持 Not Provided，不用演示数据填充。
- **不接收客户仪器数据。** 仪器观测/台账不进入此契约。
- **不提供在线上传入口。** 没有 HTTP 文件上传。操作员把完整目录复制到服务器本地 inbox
  后，再用本机接口按目录名触发导入。

## 操作步骤

1. 在 Jetson 上把已结束的巡检运行导出为 v2 BagIt 包；正式状态只能是 `completed` 或
   `completed_with_exceptions`。
2. 正常卸载介质，在 Digital Twin 服务器上将整个 `.bag` 目录复制到 inbox：

   ```bash
   cp -a /media/usb/inspection-run-run-001.bag data/imports/inbox/
   ```

3. 先在服务器本机登记 Scene Version 与 Verified Alignment（包和公共 HTTP 都不能登记）：

   ```bash
   cd services/api
   uv run python -m app.cli register-scene \
     --file ../../data/contract-fixtures/scene-registration-scene-001-v1.json \
     --operator-label "现场操作员"
   ```

4. 再导入 inbox 中的一个完整目录（只能用本机 CLI，不是 HTTP 上传）：

   ```bash
   uv run python -m app.cli import-package inspection-run-run-001.bag
   ```

5. 返回 `status=imported` 后，通过 `/api/tasks/<run_id>`、`/trajectory` 或任务回放页面
   播放。Recorded Run 显示为「现场实录，待验收」，不会因导入成功变成「现场已验收」。
   `completed_with_exceptions` 在 API 和页面中保持独立语义。
   `GET /api/imports` 只返回安全状态投影，不含归档路径。
   浏览器绘图使用 `GET /api/tasks/<run_id>/display-trajectory`（确定性降采样，不是原始证据）；
   时间轴、跳转和验收仍使用完整 Replay Trajectory（`from_seq` 翻页，单次 `limit` 最大 1,000,000）。
   历史运行必须请求 `GET /api/scenes/{scene_id}/metadata?scene_version_id=...`，
   打开导入时的 Scene Version，不得改绑到最新导航默认。

6. 本机复核验收证据（只绑定 `127.0.0.1`，不走公共 API）：

   ```bash
   uv run python -m app.cli serve-evidence-report --port 8090
   uv run python -m app.cli accept-run <run_id> \
     --operator-label "现场操作员" --first-frame pass --last-frame pass \
     --remarks "首尾帧与地标均正确"
   ```

   Operator Label 只是自报可追溯标注，不是已认证身份。公共页面只显示验收状态与时间，
   不返回验收图片或归档路径。

7. 错误验收可以追加撤销，不能改写历史。inbox 原件从不因导入成功自动删除；
   只有归档核验、现场已验收、且存在不同磁盘上的 Verified Backup 之后，才能显式清理：

   ```bash
   uv run python -m app.cli withdraw-acceptance <run_id> \
     --operator-label "复核员" --reason "首帧判读有误"
   uv run python -m app.cli verify-backup <run_id> --backup-root /mnt/backup/run-001
   uv run python -m app.cli cleanup-inbox inspection-run-run-001.bag --run-id <run_id>
   ```

服务器永远先复制到 `staging`，不直接从介质或 inbox 回放。结构、校验和或语义失败的副本
进入 `quarantine`；成功副本进入 `archive`。同一 `run_id`、同一载荷幂等返回 `duplicate`，
同一 `run_id`、不同载荷返回冲突。inbox/介质原件不由导入器删除。

BagIt 布局遵循 [RFC 8493](https://www.rfc-editor.org/rfc/rfc8493)，服务器使用
[Library of Congress bagit-python](https://github.com/LibraryOfCongress/bagit-python)
执行完整性验证，不维护自定义校验格式。
