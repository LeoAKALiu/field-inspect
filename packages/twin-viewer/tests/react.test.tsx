import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Provenance, SceneMetadata } from '@digital-twin/contracts';

vi.mock('three', async () => {
  const actual = await vi.importActual<typeof import('three')>('three');
  class FailingWebGLRenderer {
    constructor() {
      throw new Error('WebGL unavailable in this browser process');
    }
  }
  return { ...actual, WebGLRenderer: FailingWebGLRenderer };
});

import { TwinViewerView } from '../src/react.js';

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const provenance: Provenance = { source: 'simulation', status: 'simulated' };
const sceneMetadata: SceneMetadata = {
  scene_id: 'react-failure-scene',
  name: 'React failure test',
  coordinate_system: 'scene_local_yup',
  units: 'm',
  up_axis: 'Y',
  bounds_min: { x: -1, y: -1, z: -1 },
  bounds_max: { x: 1, y: 1, z: 1 },
  length_m: 2,
  route: [],
  source_type: 'simulation',
  provenance,
};

describe('TwinViewerView initialization failure', () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    container?.remove();
    root = undefined;
    container = undefined;
  });

  it('reports renderer construction errors through onViewerError', async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const onViewerError = vi.fn();

    await act(async () => {
      root?.render(
        <TwinViewerView
          sceneMetadata={sceneMetadata}
          meshUrl={null}
          pointCloudUrl={null}
          displayMode="overlay"
          vehiclePose={null}
          trajectory={[]}
          sensorDevices={[]}
          detectionEvents={[]}
          playbackTime={0}
          selectedObjectId={null}
          cameraCommand={null}
          onViewerError={onViewerError}
        />,
      );
    });

    expect(onViewerError).toHaveBeenCalledWith(
      expect.objectContaining({
        code: 'INITIALIZATION_FAILED',
        message: expect.stringContaining('WebGL unavailable'),
      }),
    );
  });
});
