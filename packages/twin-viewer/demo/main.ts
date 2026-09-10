import { TwinViewer } from '../src/TwinViewer.js';
import type {
  CameraCommand,
  DetectionEvent,
  SceneMetadata,
  SensorDevice,
  TwinDisplayMode,
  TwinTrajectoryPoint,
  Provenance,
} from '@digital-twin/contracts';

/**
 * Packaging file for LIRIS raw OBJ/PLY (Z-up source → Y-up via transformMatrix).
 * Not the contracts SceneMetadata — demo maps into contracts shape below.
 */
interface LirisPackaging {
  sceneId: string;
  name?: string;
  transformMatrix: number[];
  boundingBox: {
    min: { x: number; y: number; z: number };
    max: { x: number; y: number; z: number };
  };
  recommendedRoute: { x: number; y: number; z: number }[];
  recommendedDeviceAnchors: {
    id: string;
    label?: string;
    position: { x: number; y: number; z: number };
  }[];
}

const packUrl = new URL('../../../assets/tunnel/liris/scene-metadata.json', import.meta.url);
const meshUrl = new URL('../../../assets/tunnel/liris/tunnel_mesh.obj', import.meta.url).href;
const plyUrl = new URL('../../../assets/tunnel/liris/tunnel_pointcloud.ply', import.meta.url).href;

const container = document.getElementById('viewer');
const status = document.getElementById('status')!;
const progressEl = document.getElementById('progress')!;
if (!container) throw new Error('#viewer missing');

const pack = (await fetch(packUrl).then((r) => r.json())) as LirisPackaging;
const provenance: Provenance = { source: 'simulation', status: 'simulated' };
const sceneId = pack.sceneId;
const dx = pack.boundingBox.max.x - pack.boundingBox.min.x;

const sceneMetadata: SceneMetadata = {
  scene_id: sceneId,
  name: pack.name ?? sceneId,
  coordinate_system: 'scene_local_yup',
  units: 'm',
  up_axis: 'Y',
  bounds_min: { ...pack.boundingBox.min },
  bounds_max: { ...pack.boundingBox.max },
  length_m: dx,
  route: pack.recommendedRoute.map((p) => ({ ...p })),
  mesh_url: '/assets/tunnel/liris/tunnel_mesh.obj',
  pointcloud_url: '/assets/tunnel/liris/tunnel_pointcloud.ply',
  source_type: 'simulation',
  provenance,
};

const trajectory: TwinTrajectoryPoint[] = pack.recommendedRoute.map((p, i) => ({
  time: i * 5,
  position: { ...p },
  heading_deg: undefined,
}));

const deviceTypes = [
  'delamination',
  'displacement',
  'convergence',
  'stress',
  'temperature',
  'humidity',
  'gas',
] as const;

const sensorDevices: SensorDevice[] = pack.recommendedDeviceAnchors.slice(0, 8).map((a, i) => ({
  id: a.id,
  scene_id: sceneId,
  name: a.label ?? a.id,
  type: deviceTypes[i % deviceTypes.length]!,
  unit: 'n/a',
  position: { ...a.position },
  status: i % 4 === 3 ? 'offline' : 'online',
  source_type: 'simulation',
  provenance,
}));

const detectionEvents: DetectionEvent[] = [
  {
    id: 'evt-crack-demo-1',
    scene_id: sceneId,
    task_id: 'demo-task-1',
    type: 'crack',
    severity: 'high',
    status: 'open',
    position: {
      ...pack.recommendedRoute[Math.floor(pack.recommendedRoute.length / 3)]!,
      y: pack.recommendedRoute[Math.floor(pack.recommendedRoute.length / 3)]!.y + 1.2,
    },
    description: 'Demo crack candidate',
    confidence: 0.82,
    detected_at: new Date().toISOString(),
    source_type: 'simulation',
    provenance,
  },
  {
    id: 'evt-spall-demo-2',
    scene_id: sceneId,
    task_id: 'demo-task-1',
    type: 'spalling',
    severity: 'medium',
    status: 'open',
    position: {
      ...pack.recommendedRoute[Math.floor((pack.recommendedRoute.length * 2) / 3)]!,
      y:
        pack.recommendedRoute[Math.floor((pack.recommendedRoute.length * 2) / 3)]!.y + 1.2,
    },
    description: 'Demo spalling candidate',
    confidence: 0.71,
    detected_at: new Date().toISOString(),
    source_type: 'simulation',
    provenance,
  },
];

const viewer = new TwinViewer(container, {
  sceneMetadata,
  meshUrl,
  pointCloudUrl: plyUrl,
  displayMode: 'overlay',
  options: {
    meshOpacity: 0.55,
    transformMatrix: pack.transformMatrix,
  },
});

viewer.onLoadProgress((p) => {
  progressEl.textContent = `${p.stage} ${(p.ratio * 100).toFixed(0)}%`;
});
viewer.onViewerError((e) => {
  status.textContent = `ERROR [${e.code}] ${e.message}`;
  status.classList.add('error');
});
viewer.onViewerReady(() => {
  status.textContent = 'Ready — click mesh/points/devices/events';
  progressEl.textContent = '100%';
});
viewer.onObjectSelect((e) => {
  status.textContent = `objectSelect kind=${e.kind} id=${e.id}\n${JSON.stringify(e.position)}`;
});
viewer.onCoordinatePick((e) => {
  status.textContent = `coordinatePick\n${JSON.stringify(e.position)}`;
});

viewer.setTrajectory(trajectory);
viewer.setSensorDevices(sensorDevices);
viewer.setDetectionEvents(detectionEvents);
viewer.setPlaybackTime(0);

const t0 = performance.now();
void viewer.load().then(() => {
  const ms = performance.now() - t0;
  console.info(`[TwinViewer demo] load complete in ${ms.toFixed(0)} ms`);
  (window as unknown as { __twinLoadMs?: number }).__twinLoadMs = ms;
});

const modeSel = document.getElementById('mode') as HTMLSelectElement;
const timeInput = document.getElementById('time') as HTMLInputElement;
const timeVal = document.getElementById('timeVal')!;
const opacityInput = document.getElementById('opacity') as HTMLInputElement;

modeSel.addEventListener('change', () => {
  viewer.setDisplayMode(modeSel.value as TwinDisplayMode);
});
timeInput.addEventListener('input', () => {
  const t = Number(timeInput.value);
  timeVal.textContent = t.toFixed(1);
  viewer.setPlaybackTime(t);
});
opacityInput.addEventListener('input', () => {
  viewer.setMeshOpacity(Number(opacityInput.value));
});

function cam(type: CameraCommand['type'], objectId?: string): void {
  if (type === 'focusObject') {
    viewer.executeCameraCommand({ type, objectId: objectId! });
  } else {
    viewer.executeCameraCommand({ type });
  }
}

document.getElementById('cam-reset')!.onclick = () => cam('reset');
document.getElementById('cam-top')!.onclick = () => cam('top');
document.getElementById('cam-perspective')!.onclick = () => cam('perspective');
document.getElementById('cam-front')!.onclick = () => cam('front');
document.getElementById('cam-follow')!.onclick = () => cam('followVehicle');
document.getElementById('cam-focus-evt')!.onclick = () => cam('focusObject', 'evt-crack-demo-1');
document.getElementById('cam-focus-dev')!.onclick = () =>
  cam('focusObject', sensorDevices[0]!.id);
document.getElementById('btn-fail')!.onclick = () => {
  const bad = new TwinViewer(document.createElement('div'), {
    sceneMetadata,
    meshUrl: '/does-not-exist.obj',
    pointCloudUrl: plyUrl,
    options: { transformMatrix: pack.transformMatrix },
  });
  bad.onViewerError((e) => {
    status.textContent = `load-fail demo: ${e.message}`;
    bad.dispose();
  });
  void bad.load().catch(() => undefined);
};

(window as unknown as { twinViewer: TwinViewer }).twinViewer = viewer;
