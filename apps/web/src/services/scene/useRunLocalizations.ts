import { useCallback, useEffect, useState } from 'react';
import type { InstrumentLocalization } from '@digital-twin/contracts';

export type MarkerLocalization = {
  localization: InstrumentLocalization; captured_at: string; dictionary: string; marker_id: number;
  attempt_id: string; capture_attempt_seq: number; report_id: string; image_sha256: string;
  reason: string; gaps: string[]; conditional_max_position_std_m: number | null;
  timing_displacement_bound_m: number | null;
};
type Page = { run_id: string; scene_version_id: string; alignment_id: string;
  items: MarkerLocalization[]; next_cursor: string | null };
const EMPTY: MarkerLocalization[] = [];

export function useRunLocalizations(runId: string | null, version: string | null | undefined,
  alignment: string | null | undefined) {
  const binding = JSON.stringify([runId, version, alignment]);
  const [pagination, setPagination] = useState({ binding, cursors: [''] });
  const cursors = pagination.binding === binding ? pagination.cursors : [''];
  const cursor = cursors[cursors.length - 1] ?? '';
  const [revision, setRevision] = useState(0);
  const key = JSON.stringify([binding, cursor, revision]);
  const [state, setState] = useState<{ key: string; data: Page | null; error: string } | null>(null);
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (!runId || !version || !alignment) return;
    const controller = new AbortController();
    setState({ key, data: null, error: '' });
    fetch(`/api/instruments/runs/${encodeURIComponent(runId)}/marker-localizations?limit=25&cursor=${encodeURIComponent(cursor)}`,
      { signal: controller.signal }).then(async response => {
        if (!response.ok) throw new Error(`观测定位读取失败 (${response.status})，请核对授权及运行、报告和采集来源。`);
        const page = await response.json() as Page;
        if (page.run_id !== runId || page.scene_version_id !== version || page.alignment_id !== alignment
            || page.items.some(item => item.localization.run_id !== runId || item.localization.source_type !== 'replay')) {
          throw new Error('观测定位与当前运行或场景版本不一致。');
        }
        if (!controller.signal.aborted) setState({ key, data: page, error: '' });
      }).catch(error => {
        if (!controller.signal.aborted) setState({ key, data: null, error: String(error.message) });
      });
    return () => controller.abort();
  }, [runId, version, alignment, cursor, key]);

  const current = state?.key === key ? state : null;
  const data = current?.data;
  const error = current?.error || (runId && (!version || !alignment) ? '当前运行缺少绑定的场景版本或对齐。' : '');
  return { rows: data?.items ?? EMPTY, error, loading: Boolean(runId && !data && !error),
    pageNumber: cursors.length, canPrevious: cursors.length > 1 && Boolean(data || error), canNext: Boolean(data?.next_cursor), refresh,
    previous: () => setPagination({ binding, cursors: cursors.slice(0, -1) }),
    next: () => { if (data?.next_cursor) setPagination({ binding, cursors: [...cursors, data.next_cursor] }); },
  };
}
