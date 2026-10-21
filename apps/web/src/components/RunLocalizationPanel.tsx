import type { MarkerLocalization, useRunLocalizations } from '../services/scene/useRunLocalizations';
import { StationEvidenceDetails } from './StationEvidenceDetails';

export function RunLocalizationPanel({ runId, page, selected, onSelect, onClose, trajectoryStart, trajectoryEnd }: {
  runId: string; page: ReturnType<typeof useRunLocalizations>; selected: MarkerLocalization | undefined;
  onSelect: (entry: MarkerLocalization, focus: boolean) => void;
  onClose: () => void;
  trajectoryStart: string | undefined; trajectoryEnd: string | undefined;
}) {
  const placed = page.rows.filter(entry => entry.localization.position).length;
  const stamp = selected ? Date.parse(selected.captured_at) : NaN;
  const covered = Number.isFinite(stamp) && trajectoryStart && trajectoryEnd
    && Date.parse(trajectoryStart) <= stamp && stamp <= Date.parse(trajectoryEnd);
  return <section className="panel-card">
    <div className="panel-header">本运行标靶观测定位 <button type="button" onClick={page.refresh}>刷新定位与报告</button></div>
    <div className="panel-body">
      <p>本页 {page.rows.length} 条处理记录，{placed} 个点位以黄色方框显示；点位为待复核的标记中心估计，设备身份另行复核。</p>
      <p>本页点位静态展示；选择记录后暂停回放，并在轨迹时间覆盖时跳转到采集时刻。</p>
      {page.error && <p role="alert">{page.error}</p>}
      {page.loading && <p>正在读取本运行的处理记录…</p>}
      {!page.loading && !page.error && !page.rows.length && <p>尚无可关联的标靶处理记录，请先在站点采集详情中派生观测，再刷新。</p>}
      {page.rows.map(entry => <article key={entry.localization.localization_id} style={{ marginBottom: 12, overflowWrap: 'anywhere' }}>
        <p>{entry.dictionary} / {entry.marker_id} · {entry.captured_at} · {entry.localization.position ? '有位置估计，待复核' : '未输出位置'}</p>
        <small>{entry.localization.localization_id}</small>
        {entry.localization.position && <p>场景坐标（米）：X {entry.localization.position.x.toFixed(3)} / Y {entry.localization.position.y.toFixed(3)} / Z {entry.localization.position.z.toFixed(3)}</p>}
        {!entry.localization.position && <p>{entry.reason}{entry.gaps.length ? ` · ${entry.gaps.join('；')}` : ''}</p>}
        <button type="button" aria-pressed={selected?.localization.localization_id === entry.localization.localization_id}
          onClick={() => onSelect(entry, false)}>查看定位证据</button>
        <button type="button" disabled={!entry.localization.position} onClick={() => onSelect(entry, true)}>定位三维点位</button>
      </article>)}
      <div><button type="button" disabled={!page.canPrevious} onClick={page.previous}>上一页</button>
        <span> 第 {page.pageNumber} 页（每页最多 25 条，三维只显示本页点位） </span>
        <button type="button" disabled={!page.canNext} onClick={page.next}>下一页</button></div>
      {selected && <section aria-label="所选标靶定位证据">
        <h3>标靶 {selected.marker_id} · 定位证据 <button type="button" onClick={onClose}>关闭定位证据</button></h3>
        <p>采集时间：{selected.captured_at} · {covered ? '已暂停回放，采集时刻在轨迹范围内。' : '已暂停回放；采集时刻不在可用轨迹范围内，未跳转。'}</p>
        <p>位置状态：{selected.localization.position ? '估计，待现场复核' : '无位置'} · {selected.reason}</p>
        {selected.conditional_max_position_std_m != null && <p>条件最大轴标准差：{selected.conditional_max_position_std_m.toFixed(4)} 米</p>}
        {selected.timing_displacement_bound_m != null && <p>声明速度边界下的同步位移上界：{selected.timing_displacement_bound_m.toFixed(4)} 米</p>}
        <p>图像 SHA-256：<code>{selected.image_sha256}</code></p>
        <StationEvidenceDetails key={selected.report_id} runId={runId} attemptId={selected.attempt_id} reportId={selected.report_id} />
      </section>}
    </div>
  </section>;
}
