/** Formal Inspection Package v2 — Jetson/Digital Twin handoff contract. */

import type { Position3D } from './domain.js';

export type InspectionPackageSourceKind = 'synthetic_contract_fixture' | 'inspection_run';
export type InspectionPackageStatus = 'completed' | 'completed_with_exceptions';
export type InspectionPackageArtifactKind = 'evidence_image' | 'pointcloud' | 'inspection_result';
export type ArucoLandmarkRole = 'required' | 'auxiliary';
export type RoutePortion = 'beginning' | 'middle' | 'end';

export interface Point2D {
  x: number;
  y: number;
}

export interface RotationRpyDeg {
  roll: number;
  pitch: number;
  yaw: number;
}

export interface Pose3D {
  position: Position3D;
  rotation_rpy_deg: RotationRpyDeg;
}

export interface PoseTolerance {
  translation_m: number;
  rotation_deg: number;
}

export interface SceneVersionRef {
  scene_id: string;
  scene_version_id: string;
  asset_sha256: string;
}

export interface AlignmentRef {
  alignment_id: string;
  evidence_sha256: string;
}

export interface InspectionPackageBagReference {
  storage_id: 'mcap';
  path: string;
}

export interface InspectionPackageReplayReference {
  trajectory_path: string;
}

export interface InspectionPackageArtifact {
  path: string;
  kind: InspectionPackageArtifactKind;
  display: boolean;
}

export interface PayloadInventoryEntry {
  path: string;
  sha256: string;
}

export interface InspectionPackageException {
  code: string;
  message: string;
}

export interface Detector {
  name: string;
  version: string;
  configuration: Record<string, unknown>;
}

export interface EvidenceLineage {
  camera_calibration_sha256: string;
  detector: Detector;
  transform_id: string;
  transform_sha256: string;
}

export interface LandmarkObservation {
  seq: number;
  mcap_frame_timestamp: string;
  corners_px: [Point2D, Point2D, Point2D, Point2D];
  pose_camera: Pose3D;
  pose_scene: Pose3D;
  translation_residual_m: number;
  rotation_residual_deg: number;
  camera_calibration_sha256: string;
  detector: Detector;
  transform_id: string;
  transform_sha256: string;
}

export interface LandmarkEvidence {
  landmark_id: string;
  marker_id: number;
  dictionary: string;
  role: ArucoLandmarkRole;
  physical_size_m: number;
  registered_pose: Pose3D;
  pose_tolerance: PoseTolerance;
  min_valid_samples: number;
  route_portion?: RoutePortion;
  annotated_image_path: string;
  lineage: EvidenceLineage;
  observations: LandmarkObservation[];
  sample_count: number;
  translation_p95_m: number;
  rotation_p95_deg: number;
}

export interface FrameEvidence {
  path: string;
  timestamp: string;
  mcap_frame_timestamp: string;
}

export interface AcceptanceEvidence {
  first_frame: FrameEvidence;
  last_frame: FrameEvidence;
  landmarks: LandmarkEvidence[];
}

export interface InspectionPackageManifest {
  schema_version: '2.0';
  source_kind: InspectionPackageSourceKind;
  run_id: string;
  package_sha256: string;
  name: string;
  status: InspectionPackageStatus;
  exceptions: InspectionPackageException[];
  started_at: string;
  ended_at: string;
  coordinate_system: 'scene_local_yup';
  scene_version: SceneVersionRef;
  alignment: AlignmentRef;
  git_commit: string;
  config_sha256: string;
  model_version: string | null;
  bag: InspectionPackageBagReference;
  replay: InspectionPackageReplayReference;
  acceptance_evidence: AcceptanceEvidence;
  artifacts: InspectionPackageArtifact[];
  payload_inventory: PayloadInventoryEntry[];
  exit_reason: string;
}

export interface InspectionPackageTrajectoryPoint {
  seq: number;
  timestamp: string;
  position: Position3D;
  heading_deg: number | null;
  speed_mps: number | null;
  battery_pct?: number | null;
}

export interface InspectionPackageTrajectory {
  schema_version: '2.0';
  run_id: string;
  scene_version_id: string;
  alignment_id: string;
  coordinate_system: 'scene_local_yup';
  points: InspectionPackageTrajectoryPoint[];
}
