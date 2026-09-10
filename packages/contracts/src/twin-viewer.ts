/**
 * TwinViewer 三维模块契约 v2（冻结）
 *
 * 本契约是前端业务层与三维引擎之间的唯一交互边界：
 * - Grok 负责实现引擎（packages/twin-viewer/**，业务方不得修改）；
 * - 前端业务页面不得直接触碰 Three.js 对象，一切三维交互经由本接口；
 * - 引擎交付前由 apps/web 内的 TwinViewerAdapter + MockViewer 占位实现本接口。
 *
 * 坐标系：本契约层统一 scene_local_yup（右手系，Y 轴向上，单位米）。
 * 引擎公共 API 实测同为 Y-up；适配器将转换集中在单点（当前恒等映射，
 * 预留 (x, y, z) → (x, -z, y) 开关），业务层无需感知。
 */

import type {
  DetectionEvent,
  Position3D,
  SceneMetadata,
  SensorDevice,
} from './domain.js';

// ---------------------------------------------------------------------------
// 输入
// ---------------------------------------------------------------------------

/** 显示模式：实体网格 | 点云 | 叠加。 */
export type TwinDisplayMode = 'mesh' | 'pointcloud' | 'overlay';

/** 车辆位姿（通常取某时刻的 VehicleState 子集）。 */
export interface VehiclePose {
  position: Position3D;
  /** 航向角（度），绕 +Y 轴。 */
  heading_deg: number;
}

/** 轨迹点（播放用，time 为相对任务开始的秒数）。 */
export interface TwinTrajectoryPoint {
  time: number;
  position: Position3D;
  heading_deg?: number;
}

/** 相机命令。focusObject 必须携带 objectId。 */
export type CameraCommand =
  | { type: 'reset' }
  | { type: 'top' }
  | { type: 'perspective' }
  | { type: 'front' }
  | { type: 'followVehicle' }
  | { type: 'focusObject'; objectId: string };

export interface TwinViewerInputs {
  sceneMetadata: SceneMetadata;
  /** 相对站点根路径的模型/点云 URL，缺省为 null（引擎显示占位体）。 */
  meshUrl: string | null;
  pointCloudUrl: string | null;
  displayMode: TwinDisplayMode;
  vehiclePose: VehiclePose | null;
  trajectory: TwinTrajectoryPoint[];
  sensorDevices: SensorDevice[];
  detectionEvents: DetectionEvent[];
  /** 当前播放时刻（相对任务开始的秒数）。 */
  playbackTime: number;
  selectedObjectId: string | null;
  cameraCommand: CameraCommand | null;
}

// ---------------------------------------------------------------------------
// 事件回调
// ---------------------------------------------------------------------------

export type TwinSelectableKind = 'device' | 'event' | 'vehicle' | 'mesh' | 'point' | 'scene';

export interface TwinObjectSelectEvent {
  id: string;
  kind: TwinSelectableKind;
  position: Position3D;
}

export interface TwinCoordinatePickEvent {
  position: Position3D;
}

export interface TwinLoadProgress {
  /** 0–1。 */
  ratio: number;
  stage: 'mesh' | 'pointcloud' | 'vehicle' | 'idle';
}

export interface TwinViewerError {
  code: string;
  message: string;
}

export interface TwinCameraState {
  position: Position3D;
  target: Position3D;
}

export interface TwinViewerCallbacks {
  onObjectSelect?: (event: TwinObjectSelectEvent) => void;
  onCoordinatePick?: (event: TwinCoordinatePickEvent) => void;
  onViewerReady?: () => void;
  onLoadProgress?: (progress: TwinLoadProgress) => void;
  onViewerError?: (error: TwinViewerError) => void;
  onCameraChanged?: (state: TwinCameraState) => void;
}

// ---------------------------------------------------------------------------
// 组件 Props（React 绑定与适配器共用的完整契约）
// ---------------------------------------------------------------------------

export interface TwinViewerProps extends TwinViewerInputs, TwinViewerCallbacks {
  /** 容器高度（默认填满父容器）。 */
  height?: number | string;
}
