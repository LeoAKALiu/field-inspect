import { PagedRecordList } from '../components/PagedRecordList';
import { ObservationReview } from '../components/ObservationReview';
import { useEffect, useState } from 'react';
import ReactECharts from 'echarts-for-react';

type Metric = { name: string; value: number; unit: string };
type Reading = { reading_id: string; captured_at: string; quality: string; metrics: Metric[] };
type Asset = { asset_id: string; name: string; scene_version_id: string; units: Record<string, string> };
type Analysis = { asset?: Asset; at?: string; asset_id: string; status: string; recommendation: string;
  history: { reading: Reading }[]; channels?: (Metric & { recommendation: string; threshold_status: string })[];
  notice?: string };
const names: Record<string, string> = { deep_base: '深基点', shallow_base: '浅基点', delta: '差值' };
const labels: Record<string, string> = { normal: '正常', attention: '关注', review: '待复核', alarm: '报警建议',
  available: '有有效历史读数', missing: '无读数', future_only: '仅有未来读数',
  no_valid_prior_reading: '此前无有效读数', configured: '已配置阈值', unconfigured: '阈值未配置' };

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const result = await fetch(`/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  const value = await result.json();
  if (!result.ok) throw new Error(value.error?.message ?? value.detail ?? `请求失败 (${result.status})`);
  return value;
}

export function InstrumentEvidencePage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selected, setSelected] = useState('');
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [assetCursor,setAssetCursor] = useState('');
  const [assetNext,setAssetNext] = useState<string|null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [identity,setIdentity] = useState<{id:string;role:string;mode:string}|null>(null);
  const [token, setToken] = useState('');
  const [at, setAt] = useState('');
  const [importKind, setImportKind] = useState('assets');
  const [source, setSource] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    let active = true;
    setBusy(true); setError('');
    fetchJson<{items:Asset[];next_cursor:string|null}>(`/instruments/asset-page?cursor=${encodeURIComponent(assetCursor)}`)
      .then(page => { if(active){setAssets(page.items);setAssetNext(page.next_cursor);} })
      .catch(e => { if (active) setError(String(e.message)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [refresh,assetCursor]);

  useEffect(() => {
    let active = true; setAnalysis(null);
    if (selected) fetchJson<Analysis>(`/instruments/assets/${encodeURIComponent(selected)}/analysis${at ? `?at=${encodeURIComponent(at)}` : ''}`)
      .then(value => { if (active) setAnalysis(value); })
      .catch(e => { if (active) setError(String(e.message)); });
    return () => { active = false; };
  }, [selected, at, refresh]);

  useEffect(()=>{let active=true;fetchJson<{id:string;role:string;mode:string}>('/access/me').then(v=>{if(active)setIdentity(v);}).catch(()=>{if(active)setIdentity(null);});return()=>{active=false;};},[refresh]);
  async function login() {
    try {
      await fetchJson('/access/login', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
      setToken(''); setRefresh(x => x + 1); setMessage('已建立本次会话，可返回其他页面。');
    } catch (e) { setError(String(e)); }
  }
  async function submit() {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await fetchJson<{ rejected?: number; saved?: number }>(`/instruments/${importKind}`, { method: 'POST', body: JSON.stringify(JSON.parse(source)) });
      setMessage(result.rejected ? `已保存 ${result.saved ?? 0} 行，拒收 ${result.rejected} 行；请核对服务端结果后修正。` : '记录已保存；请查看来源和复核状态。'); if (!result.rejected) setSource(''); else setError(JSON.stringify(result)); setRefresh(x => x + 1);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  function download() {
    const blob = new Blob([JSON.stringify(analysis, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
    anchor.href = url; anchor.download = 'field-instrument-report.json'; anchor.click(); URL.revokeObjectURL(url);
  }

  const chosen = assets.find(a => a.asset_id === selected) ?? analysis?.asset;
  const channels = ['deep_base', 'shallow_base', 'delta'];
  const option = {
    tooltip: { trigger: 'axis' }, legend: { data: channels.map(c => names[c]) },
    grid: { left: 60, right: 125, top: 45, bottom: 70 },
    xAxis: { type: 'time' },
    yAxis: channels.map((c, i) => ({ type: 'value', name: `${names[c]} (${chosen?.units[c] ?? '单位未配置'})`,
      position: i === 0 ? 'left' : 'right', offset: i === 2 ? 65 : 0 })),
    dataZoom: [{ type: 'inside' }, { type: 'slider' }],
    series: channels.map((c, i) => ({ name: names[c], type: 'line', yAxisIndex: i, connectNulls: false,
      data: analysis?.history?.map(({ reading }) => [reading.captured_at,
        reading.quality === 'good' ? reading.metrics.find(m => m.name === c)?.value ?? null : null]) ?? [] })),
  };

  return <section style={{ padding: 24, display: 'grid', gap: 20, maxWidth: 1400, margin: 'auto' }}>
    <header><h2>设备证据与三通道读数</h2><p>读取本机服务中的登记设备、观测和历史读数。缺失数据保持为空，分析是供人员复核的规则建议。</p></header>
    <p>当前身份：{identity ? `${identity.id} · ${identity.role} · ${identity.mode === 'individual' ? '独立身份' : '共享/本机模式'}` : '未认证'}</p><details><summary>访问授权</summary><label>操作密钥 <input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} /></label>
      <button onClick={login}>登录</button><button onClick={async () => { try { await fetchJson('/access/logout', { method: 'POST' }); setRefresh(x => x + 1); } catch(e) {setError(String(e));} }}>退出</button>
      <p>密钥由部署人员配置，不写入项目或浏览器存储。默认本机模式无需输入。</p></details>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}{busy && <p role="status">正在读取或保存…</p>}
    <div><label>设备 <select value={selected} onChange={e => setSelected(e.target.value)}><option value="">请选择登记设备</option>
      {selected&&!assets.some(a=>a.asset_id===selected)&&<option value={selected}>{chosen?.name??selected}</option>}{assets.map(a => <option key={a.asset_id} value={a.asset_id}>{a.name} · {a.asset_id}</option>)}</select></label>
      <button onClick={()=>{setAssetCursor('');setSelected('');}}>设备首页</button><button disabled={!assetNext} onClick={()=>{if(assetNext){setAssetCursor(assetNext);setSelected('');}}}>下一页设备</button>
      <label> 回溯时刻（含时区） <input value={at} placeholder="2026-09-10T12:00:00+08:00" onChange={e => setAt(e.target.value)} /></label>
      <button onClick={() => setRefresh(x => x + 1)}>刷新</button></div>
    {!busy && assets.length === 0 && <p>尚无登记设备。先在服务器登记场景，再导入设备与经审核的数据映射。</p>}
    {analysis && <article><h3>{chosen?.name ?? analysis.asset_id}</h3>
      <p>{labels[analysis.status] ?? analysis.status} · {labels[analysis.recommendation] ?? analysis.recommendation}</p>
      <table><thead><tr><th>通道</th><th>关联读数</th><th>规则状态</th><th>建议</th></tr></thead><tbody>
        {analysis.channels?.map(m => <tr key={m.name}><td>{names[m.name]}</td><td>{m.value} {m.unit}</td>
          <td>{labels[m.threshold_status]}</td><td>{labels[m.recommendation]}</td></tr>)}</tbody></table>
      {analysis.history?.length ? <ReactECharts option={option} style={{ height: 340 }} /> : <p>本时刻之前没有历史读数。</p>}
      <p>图表显示该时刻之前的历史好质量样本；当前关联另外排除过期读数。无效样本不会用零补齐。</p>
      <button onClick={download}>导出本次分析 JSON</button><details><summary>输入、排除原因与规则来源</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(analysis, null, 2)}</pre></details>
    </article>}
    <article><h3>观测与设备关联</h3><PagedRecordList kind="observation" revision={refresh}>{o => <details key={String(o.observation_id)}>
      <summary>{String(o.observation_id)} · {String(o.captured_at)} · {String(o.source_type)}</summary>
      <pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(o,null,2)}</pre>
      <ObservationReview observationId={String(o.observation_id)}/>
      <button onClick={()=>fetchJson<Analysis>(`/instruments/observations/${encodeURIComponent(String(o.observation_id))}/analysis`).then(value=>{if(value.asset_id){setSelected(value.asset_id);setAt(value.at??String(o.captured_at));setAnalysis(value);}else{setAnalysis(null);setMessage('尚未确认唯一设备');}}).catch(e=>setError(String(e)))}>查看观测时刻读数</button>
    </details>}</PagedRecordList></article>
    <article><h3>站点尝试</h3><PagedRecordList kind="station_attempt" revision={refresh}>{row=><details key={String(row.attempt_id)}><summary>{String(row.station_id)} · {String(row.result)}</summary><pre>{JSON.stringify(row,null,2)}</pre></details>}</PagedRecordList></article>
    <details><summary>登记与导入经审核的结构化记录</summary><p>请选择正确对象，保留原字段、来源、映射版本与时区。重复标识不可覆盖已有记录。此入口不导入巡检包或原始数据库。</p>
      <label>记录类型 <select value={importKind} onChange={e => setImportKind(e.target.value)}>
        {Object.entries({ assets: '设备登记', 'records/observation': '观测', 'records/localization': '定位结果', 'records/station_attempt': '站点尝试', matches: '计算设备匹配', reviews: '人工复核', readings: '三通道读数', history: '批量历史映射导入' }).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
      </select></label><br /><label>记录 JSON<textarea style={{ width: '100%', minHeight: 200 }} value={source} onChange={e => setSource(e.target.value)} /></label>
      <button disabled={busy || !source.trim() || !identity || (identity.role !== 'operator' && !(identity.role === 'reviewer' && ['matches','reviews'].includes(importKind)))} onClick={submit}>校验并保存记录</button></details>
  </section>;
}
