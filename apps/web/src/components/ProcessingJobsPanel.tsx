import { useEffect, useState } from 'react';

type Job = { id: string; status: string; version: string; total: number; counts: Record<string,number> };
type Item = { seq: number; attempt_id: string; capture_seq: number; status: string; attempts: number; report_id: string | null; error: string | null };
type Page<T> = { items: T[]; next_cursor: string | number | null };
const labels: Record<string,string> = { queued:'等待处理', running:'处理中', paused:'已暂停', completed:'处理结束',
  completed_with_issues:'处理结束（有失败或缺口）', succeeded:'已生成报告', blocked:'输入缺口', failed:'处理失败' };
async function request<T>(path: string, signal?: AbortSignal, method='GET'): Promise<T> {
  const response = await fetch(`/api/instruments/${path}`, { method, signal });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message ?? `请求失败 (${response.status})`);
  return value as T;
}

export function ProcessingJobsPanel({ runId }: { runId: string }) {
  const [jobs, setJobs] = useState<Job[]>([]); const [selected, setSelected] = useState('');
  const [current, setCurrent] = useState<Job | null>(null); const [items, setItems] = useState<Item[]>([]);
  const [jobCursors, setJobCursors] = useState<string[]>(['']); const [nextJobs, setNextJobs] = useState<string | null>(null);
  const [itemCursors, setItemCursors] = useState<number[]>([0]); const [nextItems, setNextItems] = useState<number | null>(null);
  const [revision, setRevision] = useState(0); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const jobCursor = jobCursors[jobCursors.length-1] ?? ''; const itemCursor = itemCursors[itemCursors.length-1] ?? 0;
  useEffect(() => {
    const controller = new AbortController(); setJobs([]); setNextJobs(null);
    request<Page<Job>>(`jobs?run_id=${encodeURIComponent(runId)}&cursor=${encodeURIComponent(jobCursor)}&limit=10`,controller.signal)
      .then(page => { if (!controller.signal.aborted) { setJobs(page.items); setNextJobs(page.next_cursor as string | null); } })
      .catch(e => { if (!controller.signal.aborted) setError(String(e.message)); });
    return () => controller.abort();
  },[runId,jobCursor,revision]);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    setCurrent(null); setItems([]); setNextItems(null); if (!selected) return;
    async function refresh() {
      try {
        const [job,page] = await Promise.all([request<Job>(`jobs/${encodeURIComponent(selected)}`,controller.signal),
          request<Page<Item>>(`jobs/${encodeURIComponent(selected)}/items?cursor=${itemCursor}&limit=25`,controller.signal)]);
        if (controller.signal.aborted) return;
        setCurrent(job); setItems(page.items); setNextItems(page.next_cursor as number | null);
        if (job.status === 'queued' || job.status === 'running' || (job.counts.running ?? 0)>0) timer = setTimeout(refresh,2000);
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
    }
    void refresh(); return () => { controller.abort(); if (timer) clearTimeout(timer); };
  },[selected,itemCursor,revision]);
  async function action(kind: string) {
    setBusy(true); setError('');
    try {
      const job = await request<Job>(kind === 'create' ? `runs/${encodeURIComponent(runId)}/jobs` : `jobs/${encodeURIComponent(selected)}/${kind}`,undefined,'POST');
      setSelected(job.id); setItemCursors([0]); setRevision(value => value+1);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <section className="panel-card" style={{ marginTop:16 }}>
    <div className="panel-header">批量派生处理 <button disabled={busy} onClick={() => action('create')}>创建 / 打开本运行批次</button></div>
    <div className="panel-body">
      <p>对已索引的成功采集逐项生成报告。服务重启可续跑；处理结束不代表识别或验收通过。</p>
      {error && <p role="alert">{error}</p>}
      {!jobs.length && <p>本页没有批次。</p>}
      {jobs.map(job => <button key={job.id} onClick={() => { setSelected(job.id); setItemCursors([0]); }}>{job.id} · {labels[job.status] ?? job.status}</button>)}
      <div><button disabled={jobCursors.length===1} onClick={() => setJobCursors(value => value.slice(0,-1))}>上一页批次</button>
        <button disabled={!nextJobs} onClick={() => nextJobs && setJobCursors(value => [...value,nextJobs])}>下一页批次</button></div>
      {current && <><p>{current.id} · {labels[current.status] ?? current.status} · {current.version}</p>
        <p>共 {current.total} 项：{Object.entries(current.counts).map(([key,value]) => `${labels[key] ?? key} ${value}`).join(' / ')}</p>
        <button disabled={busy || !['queued','running'].includes(current.status)} onClick={() => action('pause')}>当前项结束后暂停</button>
        <button disabled={busy || current.status!=='paused'} onClick={() => action('resume')}>继续未完成项</button>
        <button disabled={busy || !(current.counts.failed>0)} onClick={() => action('retry_failed')}>重试失败项</button>
        <button disabled={busy} onClick={() => setRevision(value => value+1)}>刷新</button>
        {items.map(item => <p key={item.seq}>#{item.seq} · {item.attempt_id} / 采集 {item.capture_seq} · {labels[item.status] ?? item.status}
          · 已尝试 {item.attempts} 次 {item.error && `· ${item.error}`} {item.report_id && `· 报告 ${item.report_id}`}</p>)}
        <button disabled={itemCursors.length===1} onClick={() => setItemCursors(value => value.slice(0,-1))}>上一页明细</button>
        <button disabled={!nextItems} onClick={() => nextItems && setItemCursors(value => [...value,nextItems])}>下一页明细</button>
        <p>输入缺口不能通过重试修复；保留原归档，按缺口补齐新采集。暂停期间当前项可能仍在结束。</p>
      </>}
    </div>
  </section>;
}
