import { describe, expect, it } from 'vitest';
import { CONTRACTS_VERSION, INSPECTION_PACKAGE_CONTRACT_VERSION, RUN_BUNDLE_CONTRACT_VERSION } from './index';
import type {
  InspectionPackageManifest,
  InspectionRunBundleManifest,
  SceneMetadata,
  SourceType,
  TwinViewerProps,
} from './index';

describe('contracts versions', () => {
  it('exports a version', () => {
    expect(CONTRACTS_VERSION).toBe('0.3.0');
    expect(RUN_BUNDLE_CONTRACT_VERSION).toBe('1.0');
    expect(INSPECTION_PACKAGE_CONTRACT_VERSION).toBe('2.0');
  });

  it('defines the three data source types', () => {
    const sources: SourceType[] = ['simulation', 'replay', 'live_pending'];
    expect(sources).toHaveLength(3);
  });

  it('SceneMetadata declares the Y-up local coordinate system', () => {
    const meta: SceneMetadata = {
      scene_id: 's1',
      name: 'demo',
      coordinate_system: 'scene_local_yup',
      units: 'm',
      up_axis: 'Y',
      bounds_min: { x: 0, y: 0, z: 0 },
      bounds_max: { x: 1, y: 1, z: 1 },
      length_m: 1,
      route: [],
      route_provenance: {
        method: 'pointcloud_auto_corridor_centerline',
        algorithm_version: '2.0.0',
        source_asset: 'tunnel_pointcloud.ply',
        source_sha256: 'abc123',
        source_point_count: 100,
        generated_point_count: 2,
        status: 'estimated',
        disclaimer: '算法估计，非实测路线',
      },
      source_type: 'simulation',
      provenance: { source: 'simulation', status: 'simulated' },
    };
    expect(meta.up_axis).toBe('Y');
    expect(meta.route_provenance?.status).toBe('estimated');
  });

  it('TwinViewerProps composes inputs and callbacks', () => {
    const props: TwinViewerProps = {
      sceneMetadata: {
        scene_id: 's1',
        name: 'demo',
        coordinate_system: 'scene_local_yup',
        units: 'm',
        up_axis: 'Y',
        bounds_min: { x: 0, y: 0, z: 0 },
        bounds_max: { x: 1, y: 1, z: 1 },
        length_m: 1,
        route: [],
        source_type: 'simulation',
        provenance: { source: 'simulation', status: 'simulated' },
      },
      meshUrl: null,
      pointCloudUrl: null,
      displayMode: 'mesh',
      vehiclePose: null,
      trajectory: [],
      sensorDevices: [],
      detectionEvents: [],
      playbackTime: 0,
      selectedObjectId: null,
      cameraCommand: { type: 'reset' },
      onViewerReady: () => {},
    };
    expect(props.displayMode).toBe('mesh');
  });

  it('labels offline run bundles without claiming live customer data', () => {
    const manifest: InspectionRunBundleManifest = {
      schema_version: '1.0',
      source_kind: 'synthetic_contract_fixture',
      run_id: 'run-1',
      scene_id: 'scene-001',
      name: 'fixture',
      status: 'completed_with_exceptions',
      started_at: '2026-08-23T04:00:00Z',
      ended_at: '2026-08-23T04:01:00Z',
      coordinate_system: 'scene_local_yup',
      alignment_id: 'synthetic-alignment-v1',
      git_commit: 'deadbeef',
      config_sha256: '0'.repeat(64),
      model_version: null,
      bag: null,
      replay: { trajectory_path: 'replay/trajectory.json' },
      artifacts: [],
      exit_reason: 'synthetic_fixture_complete',
    };
    expect(manifest.source_kind).toBe('synthetic_contract_fixture');
    expect(manifest.replay.trajectory_path).toBe('replay/trajectory.json');
  });

  it('locks Formal Inspection Package v2 required identities', () => {
    const manifest: InspectionPackageManifest = {
      schema_version: '2.0',
      source_kind: 'inspection_run',
      run_id: 'golden-run-v2-completed',
      package_sha256: '0'.repeat(64),
      name: 'fixture',
      status: 'completed_with_exceptions',
      exceptions: [{ code: 'display_artifact_not_provided', message: 'point cloud absent' }],
      started_at: '2026-08-24T02:00:00Z',
      ended_at: '2026-08-24T02:00:10Z',
      coordinate_system: 'scene_local_yup',
      scene_version: {
        scene_id: 'scene-001',
        scene_version_id: 'scene-001-v1',
        asset_sha256: '1'.repeat(64),
      },
      alignment: {
        alignment_id: 'alignment-scene-001-v1',
        evidence_sha256: '2'.repeat(64),
      },
      git_commit: 'deadbeef',
      config_sha256: '3'.repeat(64),
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
              position: { x: 0, y: 1, z: 0 },
              rotation_rpy_deg: { roll: 0, pitch: 0, yaw: 0 },
            },
            pose_tolerance: { translation_m: 0.05, rotation_deg: 2 },
            min_valid_samples: 1,
            route_portion: 'beginning',
            annotated_image_path: 'acceptance/landmarks/aruco-begin-01.png',
            lineage: {
              camera_calibration_sha256: '5'.repeat(64),
              detector: { name: 'opencv_aruco', version: '4.10.0', configuration: {} },
              transform_id: 'camera-to-scene-scene-001-v1',
              transform_sha256: '6'.repeat(64),
            },
            observations: [
              {
                seq: 0,
                mcap_frame_timestamp: '2026-08-24T02:00:01.100Z',
                corners_px: [
                  { x: 1, y: 1 },
                  { x: 2, y: 1 },
                  { x: 2, y: 2 },
                  { x: 1, y: 2 },
                ],
                pose_camera: {
                  position: { x: 0, y: 0, z: 1 },
                  rotation_rpy_deg: { roll: 0, pitch: 0, yaw: 0 },
                },
                pose_scene: {
                  position: { x: 0, y: 1, z: 0 },
                  rotation_rpy_deg: { roll: 0, pitch: 0, yaw: 0 },
                },
                translation_residual_m: 0.01,
                rotation_residual_deg: 0.4,
                camera_calibration_sha256: '5'.repeat(64),
                detector: { name: 'opencv_aruco', version: '4.10.0', configuration: {} },
                transform_id: 'camera-to-scene-scene-001-v1',
                transform_sha256: '6'.repeat(64),
              },
            ],
            sample_count: 1,
            translation_p95_m: 0.01,
            rotation_p95_deg: 0.4,
          },
        ],
      },
      artifacts: [],
      payload_inventory: [{ path: 'bags/run.mcap', sha256: '4'.repeat(64) }],
      exit_reason: 'typed_fixture',
    };
    expect(manifest.schema_version).toBe('2.0');
    expect(manifest.alignment.evidence_sha256).toHaveLength(64);
    expect(manifest.status).toBe('completed_with_exceptions');
  });
});
