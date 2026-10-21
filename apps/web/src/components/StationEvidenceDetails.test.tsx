import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { StationEvidenceDetails } from './StationEvidenceDetails';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it('shows the specified capture and clears it before loading another report; rejects a mismatched image', async () => {
  const requests: { url: string; resolve: (value: Response) => void; signal?: AbortSignal | null }[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, options?: RequestInit) => new Promise<Response>(resolve => {
    requests.push({ url, resolve, signal: options?.signal });
  })));
  const container = document.createElement('div');
  const root = createRoot(container);
  const renders: string[] = [];
  const observer = new MutationObserver(() => renders.push(container.innerHTML));
  observer.observe(container, { subtree: true, childList: true, attributes: true });
  function respond(start: number, run: string, report: string, imageSha: string) {
    for (const request of requests.slice(start, start + 2)) {
      const value = request.url.includes('station_evidence') ? {
        run_id: run, attempt_id: `attempt-${run}`, execution_id: 'execution', artifact_sha256: 'index',
        mcap_range: { bounds_kind: 'execution_to_result_status' }, captures: [{
          capture_attempt_seq: 2, state: 'captured', reason: 'fixture',
          artifact_path: 'artifacts/station-captures/fixture/2/captured.json',
          artifact_sha256: 'metadata', image_sha256: 'image',
        }],
      } : { run_id: run, attempt_id: `attempt-${run}`, report_id: report, capture_attempt_seq: 2,
        status: 'observed_2d', gaps: [], observations: [], detections: [],
        input_sha256: { 'artifacts/station-captures/fixture/2/frame.png': imageSha } };
      request.resolve(new Response(JSON.stringify(value)));
    }
  }
  try {
    await act(async () => root.render(<StationEvidenceDetails runId="a" attemptId="attempt-a" reportId="report-a" />));
    await act(async () => respond(0, 'a', 'report-a', 'image'));
    expect(container.querySelector('img')?.getAttribute('src')).toContain('/attempt-a/captures/2/image');
    renders.length = 0;
    await act(async () => root.render(<StationEvidenceDetails runId="b" attemptId="attempt-b" reportId="report-b" />));
    expect(requests.slice(0, 2).every(request => request.signal?.aborted)).toBe(true);
    expect(renders.every(html => !html.includes('/attempt-a/captures/2/image'))).toBe(true);
    expect(container.querySelector('img')).toBeNull();
    await act(async () => respond(2, 'b', 'report-b', 'another-image'));
    expect(container.textContent).toContain('序号或摘要不一致');
    expect(container.querySelector('img')).toBeNull();
  } finally {
    observer.disconnect();
    await act(async () => root.unmount());
    vi.unstubAllGlobals();
  }
});
