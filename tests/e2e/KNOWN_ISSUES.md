# E2E 已知问题与记录（Playwright 验收）

本文件由 e2e 验收代理维护，记录验收过程中发现的问题、绕过方式与运行注意事项。
最后更新：2026-08-07（分支 agent/grok-contract-v2-sync，全量 23/23 通过，含 engine-only 7 用例）。

## 测试构成与运行方式

- `chromium` project（默认）：`scaffold` + `integration`（双路径兜底验收）+ `screenshots`
  （严格 1920×1080 正式截图，真引擎硬断言）。
- `engine-acceptance` project：`engine-acceptance.spec.ts`，engine-only 正式验收，
  全部硬性失败（MockViewer 降级 / 资源非 200/206 / 视口纯黑 / 模式不可切换均判失败）。
- 全量：`pnpm exec playwright test`；仅 engine-only：
  `pnpm exec playwright test --project=engine-acceptance`。

## 三维渲染路径

- headless chromium（SwiftShader 软渲染）下 **真引擎可正常渲染**，未走 MockViewer 降级；
  所有含三维区域的页面均断言到 `.twin-adapter canvas` 可见并真正出图。
- 测试仍保留双路径断言（`waitForViewer`，见 `tests/e2e/helpers.ts`）：
  引擎 canvas 或 MockViewer「三维引擎暂不可用」占位必有其一，实际路径打印在
  测试输出的 `[viewer-path] <页面>: ...` 行。
- 引擎首帧慢（22MB OBJ + 16MB PLY 在软渲染下解析需数秒）：canvas 挂载 ≠ 已出图。
  出图完成采用双判定（`waitForEngineRendered`）：视口截图 PNG 字节数阈值
  （按视口面积缩放，上限 30KB）+ 解码 PNG 抽样像素亮度方差（lumaStd > 5）。

## 已修复的应用缺陷（验收中发现并修复）

1. **三维 canvas 在面板容器内塌缩为 1px（/、/playback、/equipment、/review 四页）**
   - 现象：`.twin-adapter`（内联 `height:100%`）父级 `.viewport-body` 只有 `min-height`，
     百分比高度解析失败 → canvas 638×1，三维区域整片黑（/inspection 页因
     `.inspection-canvas-wrapper` 为 `absolute; inset:0` 定高而不受影响）。
   - 修复：`apps/web/src/App.css` 中 `.viewport-body` 改为 `position:relative`，
     `.twin-adapter`/`.mock-viewer` 改 `position:absolute; inset:0`。
   - 死规则 `.twin-viewer-container` 已由后续提交清理（选择器全部改为 `.twin-adapter`）。

2. **前端场景选择取到已归档场景**
   - 现象：`GET /api/scenes` 按 `created_at` 升序返回，archived 的 scene-002 排在
     active 的 scene-001 之前；`useSceneData` 取 `scenes[0]` 会选中无元数据的归档场景，
     导致整个 API 数据链静默降级为本地演示数据。
   - 修复：`apps/web/src/services/scene/sceneData.ts` 优先选 `status === 'active'` 的场景。

3. **/inspection 页 HUD 显示模式按钮被悬浮面板遮挡**
   - 现象：`.floating-panel.left-panel` 覆盖三维视口左上区域，压住 HUD 的
     「实体网格 / 点云云图 / 融合叠加」按钮（Playwright 报 "intercepts pointer events"）。
   - 修复（提交 3ecf844）：`.inspection-canvas-wrapper .viewer-hud-overlay` 加左右偏移
     避开悬浮面板，≤1100px 断点复位。
   - 回归验证：`engine-acceptance.spec.ts` E2 用例在 /inspection 页实际点击三种模式按钮，
     断言 active 态与视口图像变化（不再绕到综合态势页）。

4. **twin-viewer 新 dist 的元数据契约与 LIRIS 资产 JSON 命名不一致**
   - 现象：twin-viewer 重构后 `sceneBoundsFromMetadata` 读取 `meta.bounds_min/bounds_max`，
     而 `assets/tunnel/liris/scene-metadata.json` 仍是 `boundingBox.min|max` / `sceneId`，
     引擎构造即抛 `Cannot read properties of undefined (reading 'x')`，全页面降级 MockViewer。
   - 修复：`apps/web/src/services/scene/engineMetadata.ts` 在应用侧边界做字段映射
     （`sceneId→scene_id`、`boundingBox.min|max→bounds_min|max`，旧字段原样保留）。
   - 注意：packages/twin-viewer 处于并行开发中，若引擎输入契约再次变化，
     优先在该适配点跟进，不要改 assets 或引擎包。

## 运行注意事项

5. **复核状态 PATCH 会写库（twin.db 数据漂移）**
   - test 06 每次运行会把一个 open → acknowledged（或 acknowledged → false_positive），
     种子事件的可流转状态有限，约 4–5 次全量运行后会耗尽。
   - 完全重置：停止后端服务后运行 `scripts/reset-demo-data.sh`
     （Windows：`scripts\reset-demo-data.bat`）；幂等，自动从 `data/demo/` 重新播种。

6. **8000 端口旧 dev 进程**
   - 验收开始时 8000 端口有一个 `uvicorn --reload` 旧进程（PID 78048，11:31 启动），
     服务的是旧种子数据（gas/humidity 等旧设备类型，无离层仪/位移计），已 kill；
     playwright webServer 会以当前代码与数据重新拉起后端。
   - 复跑前若 8000 被占且数据来源不明，先核对 `curl 127.0.0.1:8000/api/devices`
     是否含 `delamination`/`displacement` 类型。

## 控制台白名单

- 兜底验收（`probeConsole`）豁免：`webgl / swiftshader / automatic fallback to software /
  gpu process / three. / favicon`。
- engine-only 与正式截图（`probeConsoleStrict`）收紧为仅豁免：
  `favicon / automatic fallback to software / GroupMarkerNotSet`；
  three.js 报错与 WebGL context 创建失败一律判失败。
当前全量运行无任何白名单外错误。
