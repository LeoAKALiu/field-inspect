import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { useRecordedPointCloud } from './useRecordedPointCloud';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it('ignores a late previous-run response and releases the active blob on unmount', async () => {
  const requests: { resolve: (response: Response) => void; signal?: AbortSignal | null }[] = [];
  const createObjectURL = vi.fn(() => 'blob:run-2');
  const revokeObjectURL = vi.fn();
  vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
  vi.stubGlobal('fetch', vi.fn((_url, options?: RequestInit) => new Promise<Response>(resolve => {
    requests.push({ resolve, signal: options?.signal });
  })));
  let latest: ReturnType<typeof useRecordedPointCloud> | undefined;
  function Probe({ runId }: { runId: string }) {
    latest = useRecordedPointCloud(runId);
    return null;
  }
  const container = document.createElement('div');
  const root = createRoot(container);
  const response = () => new Response('ply\n', { headers: {
    'X-Pointcloud-Source-Points': '6', 'X-Pointcloud-Preview-Points': '3', 'X-Pointcloud-Chunks': '2',
  } });
  try {
    await act(async () => root.render(<Probe runId="run-1" />));
    await act(async () => root.render(<Probe runId="run-2" />));
    expect(requests[0]?.signal?.aborted).toBe(true);
    await act(async () => requests[0]!.resolve(response()));
    expect(createObjectURL).not.toHaveBeenCalled();
    await act(async () => requests[1]!.resolve(response()));
    expect(latest?.url).toBe('blob:run-2');
    expect(latest?.notice).toContain('3 / 6');
    await act(async () => root.unmount());
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:run-2');
  } finally {
    await act(async () => root.unmount());
    vi.unstubAllGlobals();
  }
});
