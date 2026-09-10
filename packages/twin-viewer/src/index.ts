export { TwinViewer } from './TwinViewer.js';
export type {
  TwinViewerOptions,
  TwinViewerPublicAPI,
  TwinViewerInitProps,
  DisplayMode,
  ObjectSelectEvent,
  LoadProgressEvent,
  ViewerErrorEvent,
  CameraState,
  CoordinatePickEvent,
} from './types/viewer.js';

// Re-export contracts v2 public surface used by integrators
export type {
  SourceType,
  Provenance,
  Position3D,
  SceneMetadata,
  VehicleState,
  TrajectoryPoint,
  DetectionEvent,
  SensorDevice,
  DeviceType,
  DeviceStatus,
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

export { CONTRACTS_VERSION } from '@digital-twin/contracts';

export {
  sampleTrajectory,
  trajectoryTimesSeconds,
  splitTrajectoryByTime,
  headingFromSegment,
} from './math/trajectory.js';
export type { SampledVehiclePose } from './math/trajectory.js';
export {
  applyMatrix4RowMajor,
  rowMajorToThreeMatrix,
  headingDegToQuaternion,
  positionToVector3,
  vector3ToPosition,
  IDENTITY_MATRIX_4,
} from './math/matrix.js';
