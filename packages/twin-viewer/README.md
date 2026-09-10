# @digital-twin/twin-viewer

地下工程巡检车数字孪生 — **三维引擎模块**（TypeScript + Three.js）。

## 契约来源（唯一事实）

| 层级 | 包 / 文件 |
|------|-----------|
| 共享契约 v2 | `@digital-twin/contracts`（`packages/contracts/**`） |
| 领域对象 | `domain.ts`：`SourceType`、`SensorDevice`、`DetectionEvent`、`SceneMetadata`… |
| 三维边界 | `twin-viewer.ts`：`TwinViewerProps`、`TwinTrajectoryPoint`、`CameraCommand`、回调 |

**本包不再维护 `contractsMirror.ts`。** 公共类型一律：

```ts
import type { TwinViewerProps, SensorDevice, SourceType } from '@digital-twin/contracts';
// 或从引擎包再导出：
import type { TwinViewerProps } from '@digital-twin/twin-viewer';
```

### 类型同步机制

1. `package.json` 依赖：`"@digital-twin/contracts": "workspace:*"`  
2. TypeScript / Vitest / Vite 在未更新根锁时可用路径别名解析到 `packages/contracts/src`  
3. `tests/contractsCompat.test.ts`：字段漂移必须让 **typecheck 或 vitest** 失败（含 `live` / `strain` 等旧枚举的 `@ts-expect-error`）  
4. 禁止在引擎内手写第二套 domain 类型

### Kimi 集成时需要做什么

1. 在 monorepo 根执行 `pnpm install`，**更新根 `pnpm-lock.yaml`**（本轮引擎侧只改了包内 `package.json`，按规则未动根锁）。  
2. 前端只通过 `TwinViewerProps` / `TwinViewerView` 交互，勿直接碰 Three.js。  
3. 业务坐标与 `SceneMetadata` 均为 **scene_local_yup**（Y-up 米）。  
4. 若加载尚未变换的 LIRIS 原始 OBJ/PLY，传入  
   `options.transformMatrix`（见 `assets/tunnel/liris/scene-metadata.json` 打包矩阵）；  
   若资产已是 Y-up，省略矩阵（恒等）。  
5. 播放轨迹使用 **`TwinTrajectoryPoint[]`（`time` 秒）**，不是后端 REST 的 `TrajectoryPoint`（`seq`/`timestamp`）。  
6. 卸载时 `dispose()` 或卸载 React 组件。

---

## 安装与命令

```bash
# 根目录（Kimi 更新锁文件后）
pnpm install
pnpm --filter @digital-twin/contracts build   # 若消费 dist
pnpm --filter @digital-twin/twin-viewer typecheck
pnpm --filter @digital-twin/twin-viewer lint
pnpm --filter @digital-twin/twin-viewer test
pnpm --filter @digital-twin/twin-viewer build
pnpm --filter @digital-twin/twin-viewer demo
# http://localhost:5177
```

### 本包依赖

| 依赖 | 说明 |
|------|------|
| `@digital-twin/contracts` | 契约 v2（workspace） |
| `three` | 渲染 / OBJ / PLY / OrbitControls |
| `react` / `react-dom` | peer，React 封装 |

---

## React 最小示例（契约 props）

```tsx
import { TwinViewerView } from '@digital-twin/twin-viewer/react';
import type { TwinViewerProps } from '@digital-twin/contracts';

const props: TwinViewerProps = {
  sceneMetadata, // contracts SceneMetadata
  meshUrl: sceneMetadata.mesh_url ?? null,
  pointCloudUrl: sceneMetadata.pointcloud_url ?? null,
  displayMode: 'overlay',
  vehiclePose: null,
  trajectory: [],
  sensorDevices: [],
  detectionEvents: [],
  playbackTime: 0,
  selectedObjectId: null,
  cameraCommand: null,
  onObjectSelect: (e) => console.log(e.kind, e.id),
  onCoordinatePick: (e) => console.log(e.position),
  onViewerReady: () => {},
  onLoadProgress: (p) => console.log(p.stage, p.ratio),
  onViewerError: (e) => console.error(e),
  onCameraChanged: () => {},
};

export function Page() {
  return (
    <TwinViewerView
      {...props}
      options={{ /* transformMatrix?: number[] */ }}
      style={{ height: 560 }}
    />
  );
}
```

### 回调（契约）

| 回调 | 载荷 |
|------|------|
| `onObjectSelect` | `{ id, kind, position }` |
| `onCoordinatePick` | `{ position }` |
| `onViewerReady` | void |
| `onLoadProgress` | `{ ratio, stage }` |
| `onViewerError` | `{ code, message }` |
| `onCameraChanged` | `{ position, target }` |

### 相机命令

`reset` · `top` · `perspective` · `front` · `followVehicle` · `focusObject`（必填 `objectId`）

### 显示模式

`mesh` | `pointcloud` | `overlay`（仅 visibility，不重载资源）

---

## SourceType / DeviceType（v2）

- **SourceType:** `simulation` \| `replay` \| `live_pending`  
- **Provenance.source** 与 **source_type** 一致  
- **DeviceType:** `delamination` · `displacement` · `convergence` · `stress` · `temperature` · `humidity` · `gas`

---

## 功能（保持）

OBJ/PLY 加载 · 三模式 · 相机 · 车辆轨迹 · 设备 · 异常 · 网格/点云拾取 · React · 独立 Demo

残差说明见 `contract-change-request.md`（打包矩阵、拾取细节 — **不**改 contracts）。

---

## 目录

```text
packages/twin-viewer/
  src/TwinViewer.ts
  src/react.tsx
  src/types/viewer.ts    # re-export contracts + engine options
  tests/contractsCompat.test.ts
  contract-change-request.md
  README.md
```
