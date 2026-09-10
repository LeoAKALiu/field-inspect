/**
 * TwinViewer public types — re-export contracts v2 as the single source of truth.
 *
 * Domain + TwinViewer boundary types live in `@digital-twin/contracts`.
 * Only engine-local packaging options remain here (not part of the shared freeze).
 */

export type {
  // Domain
  SourceType,
  ProvenanceStatus,
  Provenance,
  Position3D,
  SceneMetadata,
  VehicleState,
  TrajectoryPoint,
  DetectionEvent,
  EventType,
  EventSeverity,
  EventStatus,
  SensorDevice,
  DeviceType,
  DeviceStatus,
  // TwinViewer boundary
  TwinDisplayMode,
  VehiclePose,
  TwinTrajectoryPoint,
  CameraCommand,
  TwinViewerInputs,
  TwinSelectableKind,
  TwinObjectSelectEvent,
  TwinCoordinatePickEvent,
  TwinLoadProgress,
  TwinViewerError,
  TwinCameraState,
  TwinViewerCallbacks,
  TwinViewerProps,
} from '@digital-twin/contracts';

import type {
  CameraCommand,
  DetectionEvent,
  SensorDevice,
  TwinCameraState,
  TwinCoordinatePickEvent,
  TwinDisplayMode,
  TwinLoadProgress,
  TwinObjectSelectEvent,
  TwinTrajectoryPoint,
  TwinViewerError,
  TwinViewerProps,
  VehiclePose,
} from '@digital-twin/contracts';

/**
 * Engine-local options (not in contracts freeze).
 * Use transformMatrix when raw mesh/PLY are not yet in scene_local_yup
 * (e.g. LIRIS Blender Z-up packages). Identity is assumed when omitted.
 */
export interface TwinViewerOptions {
  background?: number | string;
  maxPixelRatio?: number;
  meshOpacity?: number;
  pointSize?: number;
  pointVertexColors?: boolean;
  pointColor?: number;
  trajectoryPastColor?: number;
  trajectoryFutureColor?: number;
  enableControls?: boolean;
  /**
   * Row-major 4×4: viewerP = M * assetP.
   * Default identity. LIRIS demo supplies the packaging matrix from assets.
   */
  transformMatrix?: number[];
  /** Optional camera FOV degrees (default 50). */
  fov?: number;
}

/** Imperative engine API (superset of prop-driven control). */
export interface TwinViewerPublicAPI {
  setDisplayMode(mode: TwinDisplayMode): void;
  getDisplayMode(): TwinDisplayMode;
  setVehiclePose(pose: VehiclePose | null): void;
  setTrajectory(points: TwinTrajectoryPoint[]): void;
  setSensorDevices(devices: SensorDevice[]): void;
  setDetectionEvents(events: DetectionEvent[]): void;
  setPlaybackTime(time: number): void;
  getPlaybackTime(): number;
  setSelectedObjectId(id: string | null): void;
  executeCameraCommand(command: CameraCommand): void;
  setMeshOpacity(opacity: number): void;
  fitToScene(): void;
  resize(): void;
  dispose(): void;
  getCameraState(): TwinCameraState;
  load(): Promise<void>;
}

/** Constructor/init props: contracts TwinViewerProps fields used at mount + engine options. */
export type TwinViewerInitProps = Pick<TwinViewerProps, 'sceneMetadata' | 'meshUrl' | 'pointCloudUrl'> &
  Partial<Pick<TwinViewerProps, 'displayMode'>> & {
    options?: TwinViewerOptions;
  };

// Backward-compatible aliases used in older engine call sites / docs
export type DisplayMode = TwinDisplayMode;
export type ObjectSelectEvent = TwinObjectSelectEvent;
export type LoadProgressEvent = TwinLoadProgress;
export type ViewerErrorEvent = TwinViewerError;
export type CameraState = TwinCameraState;
export type CoordinatePickEvent = TwinCoordinatePickEvent;
