import { renderToStaticMarkup } from 'react-dom/server';
import type { TwinViewerProps } from '@digital-twin/contracts';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const viewerProbe = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
  transformMatrix: [
    1, 0, 0, 0,
    0, 0, 1, 0,
    0, -1, 0, 0,
    0, 0, 0, 1,
  ],
}));

vi.mock('@digital-twin/twin-viewer/react', () => ({
  TwinViewerView: (props: Record<string, unknown>) => {
    viewerProbe.props = props;
    return null;
  },
}));

vi.mock('../../services/scene/engineMetadata', () => ({
  useEngineSceneMetadata: () => ({
    data: {
      scene_id: 'scene-001',
      transformMatrix: viewerProbe.transformMatrix,
    },
    loading: false,
    error: null,
  }),
}));

import EngineScene from './EngineScene';

const props: TwinViewerProps = {
  sceneMetadata: {
    scene_id: 'scene-001',
    name: 'LIRIS',
    coordinate_system: 'scene_local_yup',
    units: 'm',
    up_axis: 'Y',
    bounds_min: { x: -120, y: -35, z: -72 },
    bounds_max: { x: 120, y: 35, z: 72 },
    length_m: 152,
    route: [],
    source_type: 'simulation',
    provenance: { source: 'simulation', status: 'simulated' },
  },
  meshUrl: '/tunnel/liris/tunnel_mesh.obj',
  pointCloudUrl: '/tunnel/liris/tunnel_pointcloud.ply',
  displayMode: 'overlay',
  vehiclePose: null,
  trajectory: [],
  sensorDevices: [],
  detectionEvents: [],
  playbackTime: 0,
  selectedObjectId: null,
  cameraCommand: null,
};

describe('EngineScene asset coordinates', () => {
  beforeEach(() => {
    viewerProbe.props = null;
  });

  it('passes the packaged LIRIS source-to-viewer matrix to TwinViewerView', () => {
    renderToStaticMarkup(<EngineScene {...props} />);

    expect(viewerProbe.props).not.toBeNull();
    expect(viewerProbe.props?.options).toEqual({
      transformMatrix: viewerProbe.transformMatrix,
    });
  });
});
