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

it.each([
  ['pointcloud_size_limit', '点云超过预览', 'point-cloud sources exceed the 512 MiB preview limit'],
  ['pointcloud_digest_mismatch', '点云文件摘要', 'point-cloud digest mismatch'],
  ['pointcloud_invalid_format', '损坏或截断', 'PLY body is incomplete'],
  ['pointcloud_file_missing', '点云来源文件缺失', 'An archived point-cloud source file is missing'],
])('shows the specific %s failure without exposing a preview', async (code, reason, message) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { code, message } }), { status: 422 })));
  let latest: ReturnType<typeof useRecordedPointCloud> | undefined;
  function Probe() { latest = useRecordedPointCloud('run-1'); return null; }
  const root = createRoot(document.createElement('div'));
  try {
    await act(async () => root.render(<Probe />));
    expect(latest?.error).toContain(reason);
    expect(latest?.error).toContain(message);
    expect(latest?.url).toBeNull();
    expect(latest?.notice).toBe('');
  } finally {
    await act(async () => root.unmount());
    vi.unstubAllGlobals();
  }
});

it.each([[401, '访问未授权'], [409, '导入正在占用归档'], [503, '服务器点云依赖未就绪']])(
  'keeps an actionable fallback for HTTP %s without a JSON error', async (status, reason) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('unavailable', { status: Number(status) })));
    let latest: ReturnType<typeof useRecordedPointCloud> | undefined;
    function Probe() { latest = useRecordedPointCloud('run-1'); return null; }
    const root = createRoot(document.createElement('div'));
    try {
      await act(async () => root.render(<Probe />));
      expect(latest?.error).toContain(reason);
      expect(latest?.url).toBeNull();
    } finally {
      await act(async () => root.unmount());
      vi.unstubAllGlobals();
    }
  },
);
