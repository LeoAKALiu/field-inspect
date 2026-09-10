import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import type {
  SceneMetadata,
  TwinTrajectoryPoint,
  SensorDevice,
  DetectionEvent,
  Provenance,
} from '@digital-twin/contracts';

const rendererSpies = vi.hoisted(() => ({
  createOptions: vi.fn(),
  failHighPerformance: false,
  forceContextLoss: vi.fn(),
}));

vi.mock('three', async () => {
  const actual = await vi.importActual<typeof import('three')>('three');
  class MockWebGLRenderer {
    domElement: HTMLCanvasElement;
    constructor(options?: { antialias?: boolean; powerPreference?: string }) {
      rendererSpies.createOptions(options);
      if (
        rendererSpies.failHighPerformance &&
        options?.powerPreference === 'high-performance'
      ) {
        throw new Error('Error creating WebGL context with high-performance preference');
      }
      this.domElement = document.createElement('canvas');
    }
    setPixelRatio(): void {}
    setSize(width: number, height: number): void {
      this.domElement.width = width;
      this.domElement.height = height;
    }
    render(): void {}
    dispose(): void {}
    forceContextLoss(): void {
      rendererSpies.forceContextLoss();
    }
    set outputColorSpace(_v: unknown) {}
    get outputColorSpace() {
      return '';
    }
  }
  return { ...actual, WebGLRenderer: MockWebGLRenderer };
});

import { TwinViewer } from '../src/TwinViewer.js';

const provenance: Provenance = { source: 'simulation', status: 'simulated' };

const meta: SceneMetadata = {
  scene_id: 'test-scene',
  name: 'Test',
  coordinate_system: 'scene_local_yup',
  units: 'm',
  up_axis: 'Y',
  bounds_min: { x: -10, y: -2, z: -10 },
  bounds_max: { x: 10, y: 2, z: 10 },
  length_m: 20,
  route: [
    { x: 0, y: 0, z: 0 },
    { x: 5, y: 0, z: 0 },
  ],
  source_type: 'simulation',
  provenance,
};

function makeContainer(): HTMLDivElement {
  const el = document.createElement('div');
  Object.defineProperty(el, 'clientWidth', { get: () => 800 });
  Object.defineProperty(el, 'clientHeight', { get: () => 600 });
  document.body.appendChild(el);
  return el;
}

describe('TwinViewer (contracts v2 API, mocked WebGL)', () => {
  let container: HTMLDivElement;
  let viewer: TwinViewer;

  beforeEach(() => {
    rendererSpies.createOptions.mockClear();
    rendererSpies.failHighPerformance = false;
    rendererSpies.forceContextLoss.mockClear();
    container = makeContainer();
    viewer = new TwinViewer(container, {
      sceneMetadata: meta,
      meshUrl: null,
      pointCloudUrl: null,
      displayMode: 'overlay',
      options: { enableControls: false },
    });
  });

  afterEach(() => {
    viewer.dispose();
    container.remove();
  });

  it('mounts canvas', () => {
    const canvas = container.querySelector('canvas');
    expect(canvas).toBeTruthy();
    expect(canvas?.getAttribute('role')).toBe('img');
    expect(canvas?.getAttribute('aria-label')).toContain('初始化');
  });

  it('retries with conservative WebGL options when high-performance creation fails', () => {
    viewer.dispose();
    rendererSpies.createOptions.mockClear();
    rendererSpies.failHighPerformance = true;

    viewer = new TwinViewer(container, {
      sceneMetadata: meta,
      meshUrl: null,
      pointCloudUrl: null,
      displayMode: 'overlay',
      options: { enableControls: false },
    });

    expect(rendererSpies.createOptions.mock.calls).toEqual([
      [expect.objectContaining({ antialias: true, powerPreference: 'high-performance' })],
      [expect.objectContaining({ antialias: false, powerPreference: 'default' })],
    ]);
    expect(container.querySelector('canvas')).toBeTruthy();
  });

  it('prefers the conservative WebGL profile on Safari', () => {
    viewer.dispose();
    rendererSpies.createOptions.mockClear();
    const userAgent = vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 ' +
        '(KHTML, like Gecko) Version/27.0 Safari/605.1.15',
    );

    try {
      viewer = new TwinViewer(container, {
        sceneMetadata: meta,
        meshUrl: null,
        pointCloudUrl: null,
        displayMode: 'overlay',
        options: { enableControls: false },
      });
    } finally {
      userAgent.mockRestore();
    }

    expect(rendererSpies.createOptions).toHaveBeenCalledTimes(1);
    expect(rendererSpies.createOptions).toHaveBeenCalledWith(
      expect.objectContaining({ antialias: false, powerPreference: 'default' }),
    );
    expect(container.querySelector('canvas')?.dataset.webglProfile).toBe('compatibility');
  });

  it('switches display modes without throwing', () => {
    viewer.setDisplayMode('mesh');
    expect(viewer.getDisplayMode()).toBe('mesh');
    viewer.setDisplayMode('pointcloud');
    expect(viewer.getDisplayMode()).toBe('pointcloud');
    viewer.setDisplayMode('overlay');
    expect(viewer.getDisplayMode()).toBe('overlay');
  });

  it('accepts TwinTrajectoryPoint, devices, events, playback', () => {
    const traj: TwinTrajectoryPoint[] = [
      { time: 0, position: { x: 0, y: 0, z: 0 }, heading_deg: 0 },
      { time: 10, position: { x: 5, y: 0, z: 0 }, heading_deg: 0 },
    ];
    const devices: SensorDevice[] = [
      {
        id: 'dev-1',
        scene_id: 'test-scene',
        name: 'D1',
        type: 'delamination',
        unit: 'mm',
        position: { x: 1, y: 1, z: 1 },
        status: 'online',
        source_type: 'simulation',
        provenance,
      },
    ];
    const events: DetectionEvent[] = [
      {
        id: 'evt-1',
        scene_id: 'test-scene',
        task_id: 't',
        type: 'crack',
        severity: 'high',
        status: 'open',
        position: { x: 2, y: 1, z: 0 },
        detected_at: '2026-01-01T00:00:00.000Z',
        source_type: 'simulation',
        provenance,
      },
    ];
    expect(() => {
      viewer.setTrajectory(traj);
      viewer.setSensorDevices(devices);
      viewer.setDetectionEvents(events);
      viewer.setPlaybackTime(5);
      viewer.setSelectedObjectId('evt-1');
      viewer.executeCameraCommand({ type: 'reset' });
      viewer.executeCameraCommand({ type: 'top' });
      viewer.executeCameraCommand({ type: 'front' });
      viewer.executeCameraCommand({ type: 'perspective' });
      viewer.executeCameraCommand({ type: 'followVehicle' });
      viewer.executeCameraCommand({ type: 'focusObject', objectId: 'evt-1' });
      viewer.fitToScene();
    }).not.toThrow();
    expect(viewer.getPlaybackTime()).toBe(5);
  });

  it('emits contracts-shaped load progress and pick handlers', async () => {
    const ready = vi.fn();
    const progress = vi.fn();
    const pick = vi.fn();
    const select = vi.fn();
    viewer.onViewerReady(ready);
    viewer.onLoadProgress(progress);
    viewer.onCoordinatePick(pick);
    viewer.onObjectSelect(select);

    await viewer.load();
    expect(ready).toHaveBeenCalled();
    expect(progress).toHaveBeenCalled();
    const last = progress.mock.calls.at(-1)?.[0] as { ratio: number; stage: string };
    expect(last.ratio).toBe(1);
    expect(last.stage).toBe('idle');
    expect(container.querySelector('canvas')?.getAttribute('aria-label')).toMatch(
      /已就绪.*800×600/,
    );

    const hit = viewer.pickAtNdc(0, 0);
    expect(hit === null || typeof hit.position.x === 'number').toBe(true);
  });

  it('dispose stops further API use', () => {
    viewer.dispose();
    viewer.dispose();
    expect(rendererSpies.forceContextLoss).toHaveBeenCalledTimes(1);
    expect(() => viewer.setPlaybackTime(1)).toThrow(/disposed/);
  });

  it('reports WebGL context loss instead of leaving a silent black canvas', () => {
    const onError = vi.fn();
    viewer.onViewerError(onError);
    const canvas = container.querySelector('canvas');
    expect(canvas).toBeTruthy();

    const event = new Event('webglcontextlost', { cancelable: true });
    canvas!.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'WEBGL_CONTEXT_LOST' }),
    );
  });
});
