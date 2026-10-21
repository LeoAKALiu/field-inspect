import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { useRunLocalizations } from './useRunLocalizations';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it('isolates runs, aborts old requests, and rejects a response for another scene version', async () => {
  const requests: { resolve: (value: Response) => void; signal?: AbortSignal | null }[] = [];
  vi.stubGlobal('fetch', vi.fn((_url, options?: RequestInit) => new Promise<Response>(resolve => {
    requests.push({ resolve, signal: options?.signal });
  })));
  let latest: ReturnType<typeof useRunLocalizations> | undefined;
  function Probe({ runId }: { runId: string }) {
    latest = useRunLocalizations(runId, 'scene-v1', 'alignment-1');
    return null;
  }
  const root = createRoot(document.createElement('div'));
  const response = (runId: string, version = 'scene-v1') => new Response(JSON.stringify({
    run_id: runId, scene_version_id: version, alignment_id: 'alignment-1', items: [], next_cursor: null,
  }));
  try {
    await act(async () => root.render(<Probe runId="run-1" />));
    await act(async () => root.render(<Probe runId="run-2" />));
    expect(requests[0]?.signal?.aborted).toBe(true);
    await act(async () => requests[0]!.resolve(response('run-1')));
    expect(latest?.loading).toBe(true);
    await act(async () => requests[1]!.resolve(response('run-2', 'wrong-version')));
    expect(latest?.rows).toEqual([]);
    expect(latest?.error).toContain('场景版本不一致');
    await act(async () => latest?.refresh());
    await act(async () => requests[2]!.resolve(response('run-2')));
    expect(latest?.error).toBe('');
    expect(latest?.loading).toBe(false);
  } finally {
    await act(async () => root.unmount());
    vi.unstubAllGlobals();
  }
});
