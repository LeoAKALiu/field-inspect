/**
 * Contract compatibility tests.
 * Any drift of twin-viewer against @digital-twin/contracts v2 must fail here / typecheck.
 */
import { describe, expect, it, expectTypeOf } from 'vitest';
import {
  CONTRACTS_VERSION,
  type SourceType,
  type DeviceType,
  type Provenance,
  type SceneMetadata,
  type SensorDevice,
  type DetectionEvent,
  type VehicleState,
  type TrajectoryPoint,
  type TwinViewerProps,
  type TwinDisplayMode,
  type TwinTrajectoryPoint,
  type VehiclePose,
  type TwinObjectSelectEvent,
  type TwinCoordinatePickEvent,
  type TwinLoadProgress,
  type TwinViewerError,
  type TwinCameraState,
  type CameraCommand,
} from '@digital-twin/contracts';

// Also ensure package re-exports the same surface
import {
  CONTRACTS_VERSION as PKG_VERSION,
  type TwinViewerProps as PkgTwinViewerProps,
  type SourceType as PkgSourceType,
  type DeviceType as PkgDeviceType,
} from '../src/index.js';

const SOURCE_TYPES: SourceType[] = ['simulation', 'replay', 'live_pending'];
const DEVICE_TYPES: DeviceType[] = [
  'delamination',
  'displacement',
  'convergence',
  'stress',
  'temperature',
  'humidity',
  'gas',
];

function provenance(source: SourceType = 'simulation'): Provenance {
  return { source, status: 'simulated' };
}

describe('contracts v2 compatibility', () => {
  it('uses contracts version 0.2.0', () => {
    expect(CONTRACTS_VERSION).toBe('0.3.0');
    expect(PKG_VERSION).toBe(CONTRACTS_VERSION);
  });

  it('SourceType is simulation | replay | live_pending (not live)', () => {
    expect(SOURCE_TYPES).toEqual(['simulation', 'replay', 'live_pending']);
    // @ts-expect-error legacy "live" must not be assignable
    const bad: SourceType = 'live';
    void bad;
  });

  it('DeviceType includes underground monitoring kinds', () => {
    expect(DEVICE_TYPES).toContain('delamination');
    expect(DEVICE_TYPES).toContain('displacement');
    expect(DEVICE_TYPES).toContain('convergence');
    expect(DEVICE_TYPES).toContain('stress');
    // legacy mirror kinds
    // @ts-expect-error strain is not in contracts v2 DeviceType
    const bad: DeviceType = 'strain';
    void bad;
  });

  it('domain objects require source_type aligned with Provenance.source', () => {
    const src: SourceType = 'live_pending';
    const device: SensorDevice = {
      id: 'd1',
      scene_id: 's1',
      name: '离层仪-1',
      type: 'delamination',
      unit: 'mm',
      position: { x: 1, y: 2, z: 3 },
      status: 'online',
      source_type: src,
      provenance: provenance(src),
    };
    expect(device.source_type).toBe(device.provenance.source);

    const event: DetectionEvent = {
      id: 'e1',
      scene_id: 's1',
      task_id: 't1',
      type: 'crack',
      severity: 'high',
      status: 'open',
      position: { x: 0, y: 1, z: 0 },
      detected_at: '2026-01-01T00:00:00.000Z',
      source_type: 'simulation',
      provenance: provenance('simulation'),
    };
    expect(event.source_type).toBe('simulation');

    const vehicle: VehicleState = {
      task_id: 't1',
      timestamp: '2026-01-01T00:00:00.000Z',
      position: { x: 0, y: 0, z: 0 },
      heading_deg: 10,
      speed_mps: 1,
      source_type: 'replay',
      provenance: provenance('replay'),
    };
    expect(vehicle.source_type).toBe('replay');

    const traj: TrajectoryPoint = {
      task_id: 't1',
      seq: 0,
      timestamp: '2026-01-01T00:00:00.000Z',
      position: { x: 0, y: 0, z: 0 },
      source_type: 'simulation',
      provenance: provenance('simulation'),
    };
    expect(traj.seq).toBe(0);
  });

  it('SceneMetadata uses scene_local_yup contracts shape', () => {
    const meta: SceneMetadata = {
      scene_id: 'liris-tunnel-sub2',
      name: 'demo',
      coordinate_system: 'scene_local_yup',
      units: 'm',
      up_axis: 'Y',
      bounds_min: { x: -1, y: -1, z: -1 },
      bounds_max: { x: 1, y: 1, z: 1 },
      length_m: 2,
      route: [{ x: 0, y: 0, z: 0 }],
      mesh_url: '/assets/tunnel/liris/tunnel_mesh.obj',
      pointcloud_url: '/assets/tunnel/liris/tunnel_pointcloud.ply',
      source_type: 'simulation',
      provenance: provenance(),
    };
    expect(meta.coordinate_system).toBe('scene_local_yup');
    expect(meta.up_axis).toBe('Y');
  });

  it('TwinViewerProps matches contracts freeze', () => {
    const pose: VehiclePose = { position: { x: 0, y: 0, z: 0 }, heading_deg: 0 };
    const traj: TwinTrajectoryPoint[] = [
      { time: 0, position: { x: 0, y: 0, z: 0 }, heading_deg: 0 },
      { time: 10, position: { x: 10, y: 0, z: 0 } },
    ];
    const modes: TwinDisplayMode[] = ['mesh', 'pointcloud', 'overlay'];
    const cam: CameraCommand = { type: 'focusObject', objectId: 'e1' };

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
        provenance: provenance(),
      },
      meshUrl: null,
      pointCloudUrl: null,
      displayMode: 'overlay',
      vehiclePose: pose,
      trajectory: traj,
      sensorDevices: [],
      detectionEvents: [],
      playbackTime: 0,
      selectedObjectId: null,
      cameraCommand: cam,
      onObjectSelect: (_e: TwinObjectSelectEvent) => {},
      onCoordinatePick: (_e: TwinCoordinatePickEvent) => {},
      onViewerReady: () => {},
      onLoadProgress: (_p: TwinLoadProgress) => {},
      onViewerError: (_e: TwinViewerError) => {},
      onCameraChanged: (_s: TwinCameraState) => {},
    };

    expect(modes).toContain(props.displayMode);
    expectTypeOf(props).toMatchTypeOf<PkgTwinViewerProps>();
    expectTypeOf<PkgSourceType>().toEqualTypeOf<SourceType>();
    expectTypeOf<PkgDeviceType>().toEqualTypeOf<DeviceType>();
  });

  it('object select / coordinate pick event shapes', () => {
    const sel: TwinObjectSelectEvent = {
      id: 'd1',
      kind: 'device',
      position: { x: 1, y: 2, z: 3 },
    };
    const pick: TwinCoordinatePickEvent = {
      position: { x: 1, y: 2, z: 3 },
    };
    expect(sel.kind).toBe('device');
    expect(pick.position.y).toBe(2);
  });
});
