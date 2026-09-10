import { useEffect, useState } from 'react';

type Capture = { capture_attempt_seq: number; state: string; reason: string;
  artifact_path: string; artifact_sha256: string; image_sha256: string | null };
type Evidence = { attempt_id: string; execution_id: string; artifact_sha256: string;
  mcap_range: { start_ns: number; end_ns: number; bounds_kind: string }; captures: Capture[] };
type Report = { report_id: string; attempt_id: string; capture_attempt_seq: number;
  status: string; gaps: string[]; observations: string[];
  detections: { observation_id: string; dictionary: string; marker_id: number }[] };
const states: Record<string, string> = { started: '已开始', captured: '图片已持久化', failed: '采集失败',
  blocked: '缺少输入', observed_2d: '已生成二维观测，尚未定位', no_configured_marker_detected: '未发现已配置标记' };

export function StationEvidenceDetails({ runId, attemptId }: { runId: string; attemptId: string }) {
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    async function get<T,>(kind: string): Promise<T> {
      const response = await fetch(`/api/instruments/records/${kind}?run_id=${encodeURIComponent(runId)}`, { signal: controller.signal });
      if (!response.ok) throw new Error(`证据读取失败 (${response.status})`);
      return response.json() as Promise<T>;
    }
    setLoaded(false); setError(''); setEvidence(null); setReports([]);
    Promise.all([get<Evidence[]>('station_evidence'), get<Report[]>('processing_report')])
      .then(([items, results]) => { if (!controller.signal.aborted) {
        setEvidence(items.find(item => item.attempt_id === attemptId) ?? null);
        setReports(results.filter(item => item.attempt_id === attemptId)); setLoaded(true);
      } }).catch(e => { if (!controller.signal.aborted) setError(String(e.message)); });
    return () => controller.abort();
  }, [runId, attemptId]);
  async function process(sequence: number) {
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/instruments/stations/${encodeURIComponent(attemptId)}/captures/${sequence}/process`, { method: 'POST' });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error?.message ?? (typeof value.detail === 'string' ? value.detail : `处理失败 (${response.status})`));
      setReports(previous => [...previous.filter(r => r.report_id !== value.report_id), value as Report]);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <section className="panel-body" aria-label="站点采集证据详情">
    <h3>采集证据详情</h3>
    {error && <p role="alert">{error}</p>}
    {!loaded && !error && <p>正在读取…</p>}
    {loaded && !evidence && <p>尚无已索引采集档案，请先从归档重新索引。</p>}
    {evidence && <>
      <p>路线执行：{evidence.execution_id}</p><p>档案 SHA-256：<code>{evidence.artifact_sha256}</code></p>
      <p>MCAP 区间类型：{evidence.mcap_range.bounds_kind}。此区间不是本站驻留时长。</p>
      {!evidence.captures.length && <p>没有采集记录；路线完成不等于图片采集成功。</p>}
      {evidence.captures.map(c => <div key={c.artifact_path} style={{ marginBottom: 12, overflowWrap: 'anywhere' }}>
        <p>采集第 {c.capture_attempt_seq} 次 · {states[c.state] ?? c.state} · {c.reason}</p>
        <small>记录哈希：{c.artifact_sha256}{c.image_sha256 && <> · 图片哈希：{c.image_sha256}</>}</small>
        {c.state === 'captured' && <button disabled={busy} onClick={() => process(c.capture_attempt_seq)}>
          {busy ? '正在校验归档并处理…' : '派生标记观测'}</button>}
      </div>)}
      <p>原图保留在本地归档。标记代理不等于通用仪器识别，二维检测不确认三维位置或设备身份。</p>
    </>}
    {reports.map(report => <article key={report.report_id}>
      <h4>采集第 {report.capture_attempt_seq} 次：{states[report.status] ?? report.status}</h4>
      {report.gaps.length > 0 && <><p>待补齐输入 / 能力：</p><ul>{report.gaps.map(gap => <li key={gap}>{gap}</li>)}</ul></>}
      <p>观测数：{report.observations.length}；保持待复核。</p>
      {report.detections.map(d => <p key={d.observation_id}>{d.dictionary} / {d.marker_id} · {d.observation_id}</p>)}
    </article>)}
  </section>;
}
