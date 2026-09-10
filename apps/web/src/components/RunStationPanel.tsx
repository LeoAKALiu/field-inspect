import { ProcessingJobsPanel } from './ProcessingJobsPanel';
import { StationEvidenceDetails } from './StationEvidenceDetails';
import { useEffect, useState } from 'react';

type Attempt = { attempt_id: string; station_id: string; attempt_seq: number;
  ended_at: string; result: string; reason_code: string | null };

export function RunStationPanel({ runId, origin, end, onSeek }: {
  runId: string; origin: string | undefined; end: number; onSeek: (time: number) => void;
}) {
  const [data, setData] = useState<{ runId: string; rows: Attempt[] } | null>(null);
  const [cursors, setCursors] = useState<string[]>(['']);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const cursor = cursors[cursors.length - 1];
  const [selectedAttempt, setSelectedAttempt] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setData(null); setNextCursor(null); setError('');
    fetch(`/api/instruments/pages/station_attempt?run_id=${encodeURIComponent(runId)}&cursor=${encodeURIComponent(cursor ?? '')}&limit=25`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`站点加载失败 (${response.status})`); return response.json() as Promise<{ items: Attempt[]; next_cursor: string | null }>; })
      .then(page => { if (!controller.signal.aborted) { setData({ runId, rows: page.items }); setNextCursor(page.next_cursor); } })
      .catch(e => { if (!controller.signal.aborted) setError(String(e.message)); });
    return () => controller.abort();
  }, [runId, revision, cursor]);
  async function reindex() {
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/instruments/runs/${encodeURIComponent(runId)}/reindex-stations`, { method: 'POST' });
      if (!response.ok) throw new Error(`重新索引失败 (${response.status})，请检查包完整性或登录状态`);
      setCursors(['']); setRevision(value => value + 1);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const rows = data?.runId === runId ? data.rows : null;
  return <section className="panel-card">
    <div className="panel-header">站点结果与回放 <button disabled={busy} onClick={reindex}>{busy ? '正在校验归档…' : '从归档重新索引'}</button></div>
    <div className="panel-body">
      <p>跳转到结果时间对应的车辆位置；不是站点测量坐标。路线完成不代表图像识别成功，区间不代表本站驻留时长。</p>
      {error && <p role="alert">{error}</p>}
      {!rows && !error && <p>正在读取站点档案…</p>}
      {rows?.length === 0 && <p>此运行尚无已索引站点档案。旧包需重新索引，未自动补造记录。</p>}
      {rows?.slice().sort((a, b) => a.ended_at.localeCompare(b.ended_at)).map(row => {
        const seconds = origin ? (Date.parse(row.ended_at) - Date.parse(origin)) / 1000 : NaN;
        const available = Number.isFinite(seconds) && seconds >= 0 && seconds <= end;
        return <div key={row.attempt_id} className="timeline-event-item">
          <div>{row.station_id} · 第 {row.attempt_seq} 次 · {{ success: '路线完成（待复核）', skipped: '人工跳过', failed: '失败' }[row.result] ?? row.result}
            <small> {row.ended_at} {row.reason_code ?? ''}</small></div>
          <button onClick={() => setSelectedAttempt(row.attempt_id)}>查看采集证据</button>
          <button disabled={!available} onClick={() => onSeek(seconds)}>{available ? '定位回放时刻' : '超出可用轨迹时间'}</button>
        </div>;
      })}
      <div><button disabled={cursors.length === 1 || !data} onClick={() => setCursors(value => value.slice(0,-1))}>上一页</button>
        <span> 第 {cursors.length} 页（按记录 ID 分页，本页按时间显示） </span>
        <button disabled={!nextCursor || !data} onClick={() => nextCursor && setCursors(value => [...value,nextCursor])}>下一页</button></div>
      <ProcessingJobsPanel runId={runId} />
      {selectedAttempt && <><button onClick={() => setSelectedAttempt(null)}>关闭证据详情</button>
        <StationEvidenceDetails key={`${selectedAttempt}:${revision}`} runId={runId} attemptId={selectedAttempt} /></>}
    </div>
  </section>;
}
