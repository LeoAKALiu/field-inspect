import { describe, expect, it } from 'vitest';
import { INSPECTION_PACKAGE_CONTRACT_VERSION } from './index';
import type { InspectionPackageManifest, InspectionPackageTrajectory } from './inspection-package';

describe('inspection package v2', () => {
  it('exports contract version 2.0', () => {
    expect(INSPECTION_PACKAGE_CONTRACT_VERSION).toBe('2.0');
  });

  it('types a formal inspection_run v2 package with acceptance evidence', () => {
    const trajectory: InspectionPackageTrajectory = {
      schema_version: '2.0',
      run_id: 'golden-run-v2-completed',
      scene_version_id: 'scene-001-v1',
      alignment_id: 'alignment-scene-001-v1',
      coordinate_system: 'scene_local_yup',
      points: [
        {
          seq: 0,
          timestamp: '2026-08-24T02:00:01Z',
          position: { x: -52.0, y: 1.0, z: -18.0 },
          heading_deg: 12.0,
          speed_mps: 0.0,
        },
        {
          seq: 1,
          timestamp: '2026-08-24T02:00:04Z',
          position: { x: -51.8, y: 1.0, z: -17.96 },
          heading_deg: 12.0,
          speed_mps: 0.2,
        },
      ],
    };
    const manifest: InspectionPackageManifest = {
      schema_version: '2.0',
      source_kind: 'inspection_run',
      run_id: 'golden-run-v2-completed',
      package_sha256: 'a'.repeat(64),
      name: 'Formal Inspection Package v2',
      status: 'completed',
      exceptions: [],
      started_at: '2026-08-24T02:00:00Z',
      ended_at: '2026-08-24T02:00:10Z',
      coordinate_system: 'scene_local_yup',
      scene_version: {
        scene_id: 'scene-001',
        scene_version_id: 'scene-001-v1',
        asset_sha256: 'b'.repeat(64),
      },
      alignment: {
        alignment_id: 'alignment-scene-001-v1',
        evidence_sha256: 'c'.repeat(64),
      },
      git_commit: 'deadbeef',
      config_sha256: 'd'.repeat(64),
      model_version: null,
      bag: { storage_id: 'mcap', path: 'bags/run.mcap' },
      replay: { trajectory_path: 'replay/trajectory.json' },
      acceptance_evidence: {
        first_frame: {
          path: 'acceptance/first_frame.png',
          timestamp: '2026-08-24T02:00:01Z',
          mcap_frame_timestamp: '2026-08-24T02:00:01Z',
        },
        last_frame: {
          path: 'acceptance/last_frame.png',
          timestamp: '2026-08-24T02:00:07Z',
          mcap_frame_timestamp: '2026-08-24T02:00:07Z',
        },
        landmarks: [
          {
            landmark_id: 'aruco-begin-01',
            marker_id: 1,
            dictionary: '4X4_50',
            role: 'required',
            physical_size_m: 0.2,
            registered_pose: {
              position: { x: -52.0, y: 1.05, z: -18.0 },
              rotation_rpy_deg: { roll: 0, pitch: 0, yaw: 12 },
            },
            pose_tolerance: { translation_m: 0.05, rotation_deg: 2.0 },
            min_valid_samples: 3,
            route_portion: 'beginning',
            annotated_image_path: 'acceptance/landmarks/aruco-begin-01.png',
            lineage: {
              camera_calibration_sha256: 'e'.repeat(64),
              detector: {
                name: 'opencv_aruco',
                version: '4.10.0',
                configuration: { dictionary: '4X4_50' },
              },
              transform_id: 'camera-to-scene-scene-001-v1',
              transform_sha256: 'f'.repeat(64),
            },
            observations: [
              {
                seq: 0,
                mcap_frame_timestamp: '2026-08-24T02:00:01.100Z',
                corners_px: [
                  { x: 120, y: 80 },
                  { x: 220, y: 80 },
                  { x: 220, y: 180 },
                  { x: 120, y: 180 },
                ],
                pose_camera: {
                  position: { x: 0.4, y: 0, z: 1.8 },
                  rotation_rpy_deg: { roll: 0, pitch: 0, yaw: 1 },
                },
                pose_scene: {
                  position: { x: -52.0, y: 1.05, z: -18.0 },
                  rotation_rpy_deg: { roll: 0, pitch: 0, yaw: 12 },
                },
                translation_residual_m: 0.008,
                rotation_residual_deg: 0.4,
                camera_calibration_sha256: 'e'.repeat(64),
                detector: {
                  name: 'opencv_aruco',
                  version: '4.10.0',
                  configuration: { dictionary: '4X4_50' },
                },
                transform_id: 'camera-to-scene-scene-001-v1',
                transform_sha256: 'f'.repeat(64),
              },
            ],
            sample_count: 1,
            translation_p95_m: 0.008,
            rotation_p95_deg: 0.4,
          },
        ],
      },
      artifacts: [{ path: 'artifacts/preview-pointcloud.ply', kind: 'pointcloud', display: true }],
      payload_inventory: [{ path: 'bags/run.mcap', sha256: '1'.repeat(64) }],
      exit_reason: 'typed_fixture',
    };
    expect(manifest.bag.storage_id).toBe('mcap');
    expect(manifest.acceptance_evidence.landmarks[0]?.role).toBe('required');
    expect(trajectory.points).toHaveLength(2);
  });

  it('keeps completed_with_exceptions independent of completed', () => {
    const status: InspectionPackageManifest['status'] = 'completed_with_exceptions';
    expect(status).not.toBe('completed');
  });
});
