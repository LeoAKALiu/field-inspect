/** RFC 8493 BagIt payload contracts for an offline SCOUT inspection run. */

import type { Position3D } from './domain.js';

export type RunBundleSourceKind = 'synthetic_contract_fixture' | 'inspection_run';
export type RunBundleStatus =
  | 'completed'
  | 'completed_with_exceptions'
  | 'incomplete'
  | 'aborted'
  | 'failed';

export interface RunBundleBagReference {
  storage_id: 'mcap';
  path: string;
}

export interface RunBundleReplayReference {
  trajectory_path: string;
}

export type RunBundleArtifactKind =
  | 'evidence_image'
  | 'pointcloud'
  | 'inspection_result';

export interface RunBundleArtifact {
  path: string;
  kind: RunBundleArtifactKind;
  display: boolean;
}

export interface InspectionRunBundleManifest {
  schema_version: '1.0';
  source_kind: RunBundleSourceKind;
  run_id: string;
  scene_id: string;
  name: string;
  status: RunBundleStatus;
  started_at: string;
  ended_at: string;
  coordinate_system: 'scene_local_yup';
  alignment_id: string;
  git_commit: string;
  config_sha256: string;
  model_version: string | null;
  bag: RunBundleBagReference | null;
  replay: RunBundleReplayReference;
  artifacts: RunBundleArtifact[];
  exit_reason: string;
}

export interface RunBundleTrajectoryPoint {
  seq: number;
  timestamp: string;
  position: Position3D;
  heading_deg: number | null;
  speed_mps: number | null;
  battery_pct?: number | null;
}

export interface RunBundleTrajectory {
  schema_version: '1.0';
  run_id: string;
  coordinate_system: 'scene_local_yup';
  alignment_id: string;
  points: RunBundleTrajectoryPoint[];
}

export type RunBundleImportStatus = 'imported' | 'duplicate';

export interface RunBundleImportResult {
  run_id: string;
  bundle_sha256: string;
  status: RunBundleImportStatus;
  trajectory_points: number;
}
