import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { useBoundSceneMetadata, useDisplayTrajectory, useTaskTrajectory } from './sceneData';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it('hides previous trajectories and metadata in the first render and aborts all old requests', async () => {
  const requests: { url: string; resolve: (value: Response) => void; signal?: AbortSignal | null }[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, options?: RequestInit) => new Promise<Response>(resolve => {
    requests.push({ url, resolve, signal: options?.signal });
  })));
  const renders: { run: string; replay: string[]; drawing: string[]; version: string | null }[] = [];
  function Probe({ run }: { run: string }) {
    const replay = useTaskTrajectory(run, null, { fillFromSceneRoute: false });
    const drawing = useDisplayTrajectory(run, null, { fillFromSceneRoute: false });
    const metadata = useBoundSceneMetadata('scene-1', `version-${run}`);
    renders.push({ run, replay: replay.data.map(p => p.task_id),
      drawing: drawing.data.map(p => p.task_id), version: metadata.data?.scene_version_id ?? null });
    return null;
  }
  function resolve(request: typeof requests[number], run: string, wrongScene = false) {
    const point = { task_id: run, seq: 0, timestamp: '2026-10-21T00:00:00Z',
      position: { x: 1, y: 2, z: 3 }, heading_deg: 0, speed_mps: 0 };
    const value = request.url.includes('/metadata')
      ? { scene_id: wrongScene ? 'another-scene' : 'scene-1', scene_version_id: `version-${run}` }
      : request.url.includes('/display-trajectory') ? { points: [point], disclaimer: null } : [point];
    request.resolve(new Response(JSON.stringify(value)));
  }
  const root = createRoot(document.createElement('div'));
  try {
    await act(async () => root.render(<Probe run="run-1" />));
    await act(async () => requests.slice(0, 3).forEach(request => resolve(request, 'run-1')));
    expect(renders.at(-1)).toMatchObject({ replay: ['run-1'], drawing: ['run-1'], version: 'version-run-1' });
    await act(async () => root.render(<Probe run="run-2" />));
    expect(renders.filter(render => render.run === 'run-2').every(render =>
      !render.replay.length && !render.drawing.length && render.version === null)).toBe(true);
    expect(requests.slice(0, 3).every(request => request.signal?.aborted)).toBe(true);
    await act(async () => root.render(<Probe run="run-3" />));
    expect(requests.slice(3, 6).every(request => request.signal?.aborted)).toBe(true);
    await act(async () => requests.slice(3, 6).forEach(request => resolve(request, 'run-2')));
    expect(renders.at(-1)).toMatchObject({ run: 'run-3', replay: [], drawing: [], version: null });
    await act(async () => requests.slice(6, 9).forEach(request => resolve(request, 'run-3', true)));
    expect(renders.at(-1)).toMatchObject({ replay: ['run-3'], drawing: ['run-3'], version: null });
  } finally {
    await act(async () => root.unmount());
    vi.unstubAllGlobals();
  }
});
