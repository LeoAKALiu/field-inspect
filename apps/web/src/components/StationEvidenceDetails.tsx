import { useEffect, useState } from 'react';

type Capture = { capture_attempt_seq: number; state: string; reason: string;
  artifact_path: string; artifact_sha256: string; image_sha256: string | null };
type Evidence = { run_id: string; attempt_id: string; execution_id: string; artifact_sha256: string;
  mcap_range: { start_ns: number; end_ns: number; bounds_kind: string }; captures: Capture[] };
type Report = { run_id: string; report_id: string; attempt_id: string; capture_attempt_seq: number;
  input_sha256?: Record<string, string | number>;
  status: string; gaps: string[]; observations: string[];
  localization_estimates?: { observation_id: string; marker_id: number; status: string; reason: string;
    position?: { x: number; y: number; z: number }; candidate_reprojection_rmse_px?: number[];
    conditional_max_position_std_m?: number; timing_displacement_bound_m?: number }[];
  detections: { observation_id: string; dictionary: string; marker_id: number; corners_px?: number[][] }[] };
const states: Record<string, string> = { started: '已开始', captured: '图片已持久化', failed: '采集失败',
  metric_estimates_need_review: '已生成米制估计，待现场复核', blocked: '缺少输入', observed_2d: '已生成二维观测，尚未定位', no_configured_marker_detected: '未发现已配置标记' };

type DetailsProps = { runId: string; attemptId: string; reportId?: string };

export function StationEvidenceDetails(props: DetailsProps) {
  // Reset images, pagination and in-flight state before rendering a different evidence identity.
  return <EvidenceDetails key={JSON.stringify([props.runId, props.attemptId, props.reportId])} {...props} />;
}

function EvidenceDetails({ runId, attemptId, reportId }: DetailsProps) {
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [reportRevision, setReportRevision] = useState(0);
  const [cursors, setCursors] = useState<string[]>(['']);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const cursor = cursors[cursors.length - 1];
  const [loaded, setLoaded] = useState(false);
  const [imageErrors, setImageErrors] = useState<string[]>([]);
  const [imageSizes, setImageSizes] = useState<Record<string, [number, number]>>({});
  useEffect(() => {
    const controller = new AbortController();
    async function get<T,>(path: string): Promise<T> {
      const response = await fetch(`/api/instruments/${path}`, { signal: controller.signal });
      if (!response.ok) throw new Error(`证据读取失败 (${response.status})`);
      return response.json() as Promise<T>;
    }
    setLoaded(false); setError(''); setEvidence(null); setReports([]); setImageErrors([]); setImageSizes({});
    Promise.all([get<Evidence>(`records/station_evidence/${encodeURIComponent(attemptId)}`),
      reportId ? get<Report>(`records/processing_report/${encodeURIComponent(reportId)}`).then(report => ({ items: [report], next_cursor: null }))
        : get<{ items: Report[]; next_cursor: string | null }>(`pages/processing_report?run_id=${encodeURIComponent(runId)}&attempt_id=${encodeURIComponent(attemptId)}&limit=10&cursor=${encodeURIComponent(cursor ?? '')}`)])
      .then(([items, results]) => { if (!controller.signal.aborted) {
        if (items.run_id !== runId || items.attempt_id !== attemptId
            || results.items.some(report => report.run_id !== runId || report.attempt_id !== attemptId || (reportId && report.report_id !== reportId))) {
          throw new Error('采集证据或处理报告与当前运行不一致。');
        }
        if (reportId) {
          const report = results.items[0]!;
          const captures = items.captures.filter(c => c.state === 'captured' && c.capture_attempt_seq === report.capture_attempt_seq);
          const capture = captures[0];
          const image = capture?.artifact_path.replace(/captured\.json$/, 'frame.png');
          if (captures.length !== 1 || !capture?.image_sha256 || !image
              || report.input_sha256?.[image] !== capture.image_sha256) {
            throw new Error('指定报告与成功采集图片的序号或摘要不一致。');
          }
        }
        setEvidence(items);
        setReports(results.items); setNextCursor(results.next_cursor); setLoaded(true);
      } }).catch(e => { if (!controller.signal.aborted) setError(String(e.message)); });
    return () => controller.abort();
  }, [runId, attemptId, cursor, reportRevision, reportId]);
  async function process(sequence: number) {
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/instruments/stations/${encodeURIComponent(attemptId)}/captures/${sequence}/process`, { method: 'POST' });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error?.message ?? (typeof value.detail === 'string' ? value.detail : `处理失败 (${response.status})`));
      setCursors(['']); setReportRevision(v => v+1);
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
      {evidence.captures.filter(c => !reportId || c.capture_attempt_seq === reports[0]?.capture_attempt_seq).map(c => <div key={c.artifact_path} style={{ marginBottom: 12, overflowWrap: 'anywhere' }}>
        <p>采集第 {c.capture_attempt_seq} 次 · {states[c.state] ?? c.state} · {c.reason}</p>
        <small>记录哈希：{c.artifact_sha256}{c.image_sha256 && <> · 图片哈希：{c.image_sha256}</>}</small>
        {c.state === 'captured' && (imageErrors.includes(c.artifact_path)
          ? <p role="alert">证据图片未能加载，请检查归档完整性、服务状态和访问授权。</p>
          : <figure style={{ margin: '12px 0' }}>
            <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
              <img src={`/api/instruments/stations/${encodeURIComponent(attemptId)}/captures/${c.capture_attempt_seq}/image`}
                alt={`站点采集第 ${c.capture_attempt_seq} 次的原始证据图片`}
                loading="lazy" style={{ display: 'block', maxWidth: '100%', maxHeight: 480, objectFit: 'contain' }}
                onLoad={event => {
                  const img = event.currentTarget;
                  setImageSizes(sizes => ({ ...sizes, [c.artifact_path]: [img.naturalWidth, img.naturalHeight] }));
                }}
                onError={() => setImageErrors(items => [...items, c.artifact_path])} />
              {imageSizes[c.artifact_path] && <svg viewBox={`0 0 ${imageSizes[c.artifact_path]!.join(' ')}`}
                aria-label="本页处理报告中的标靶检测框"
                style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
                {reports.filter(r => r.capture_attempt_seq === c.capture_attempt_seq).flatMap(r =>
                  r.detections.filter(d => d.corners_px?.length === 4).map(d => <g key={`${r.report_id}:${d.observation_id}`}>
                    <polygon points={d.corners_px!.map(p => p.join(',')).join(' ')} fill="none" stroke="#21f5a5" strokeWidth={3} />
                    <text x={d.corners_px![0]![0]} y={Math.max(24, d.corners_px![0]![1]! - 8)}
                      fill="#21f5a5" stroke="#10232b" strokeWidth={0.5} fontSize={24}>标靶 {d.marker_id}</text>
                  </g>))}
              </svg>}
            </div>
            <figcaption>原始采集截图 · SHA-256 {c.image_sha256}</figcaption>
          </figure>)}
        {c.state === 'captured' && <button disabled={busy} onClick={() => process(c.capture_attempt_seq)}>
          {busy ? '正在校验归档并处理…' : '派生标记观测'}</button>}
      </div>)}
      <p>图片从本地归档读取并核对摘要；米制位置估计和设备身份由操作人员复核。</p>
    </>}
    <div><button disabled={busy} onClick={() => { setCursors(['']); setReportRevision(v => v+1); }}>刷新报告</button>
      {!reportId && <><button disabled={cursors.length === 1 || !loaded} onClick={() => setCursors(value => value.slice(0,-1))}>上一页报告</button>
        <span> 第 {cursors.length} 页 </span><button disabled={!nextCursor || !loaded} onClick={() => nextCursor && setCursors(value => [...value,nextCursor])}>下一页报告</button></>}</div>
    {reports.map(report => <article key={report.report_id}>
      <h4>采集第 {report.capture_attempt_seq} 次：{states[report.status] ?? report.status}</h4>
      {report.gaps.length > 0 && <><p>待补齐输入 / 能力：</p><ul>{report.gaps.map(gap => <li key={gap}>{gap}</li>)}</ul></>}
      <p>观测数：{report.observations.length}；保持待复核。</p>
      {report.localization_estimates?.map(estimate => <div key={estimate.observation_id} style={{ marginBottom: 16 }}>
        <p>标记 {estimate.marker_id}：{estimate.status === 'estimated_needs_review' ? '米制位置估计（待复核）' : '未输出位置'} · {estimate.reason}</p>
        {estimate.position && <p>场景坐标（米）：X {estimate.position.x.toFixed(3)} / Y {estimate.position.y.toFixed(3)} / Z {estimate.position.z.toFixed(3)}</p>}
        {estimate.candidate_reprojection_rmse_px && <p>候选重投影 RMSE（像素）：{estimate.candidate_reprojection_rmse_px.map(v => v.toFixed(3)).join(' / ')}</p>}
        {estimate.conditional_max_position_std_m !== undefined && <p>模型条件下最大轴标准差：{estimate.conditional_max_position_std_m.toFixed(4)} 米</p>}
        {estimate.timing_displacement_bound_m !== undefined && <p>声明速度边界下的同步位移上界：{estimate.timing_displacement_bound_m.toFixed(4)} 米</p>}
        <small>上述数值不是实测位置残差或精度保证；保留标定、独立误差与小扰动假设，需现场复核。</small>
      </div>)}
      {report.detections.map(d => <p key={d.observation_id}>{d.dictionary} / {d.marker_id} · {d.observation_id}</p>)}
    </article>)}
  </section>;
}
