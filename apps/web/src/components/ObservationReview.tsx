import { useEffect,useState } from 'react';
type Review = { review_id:string; asset_id:string|null; reason:string; operator_label:string; reviewed_at:string };
type Entry = { match:{match_id:string;status:string;asset_id:string|null;candidate_asset_ids:string[]}; localization:Record<string,unknown>; latest_review:Review|null };
type Context = { scene_version_id:string|null;matches:Entry[];localizations:Record<string,unknown>[];notice:string };
async function api<T>(path:string,body?:unknown):Promise<T>{const r=await fetch(`/api/instruments/${path}`,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw new Error(v.error?.message??'操作失败');return v;}
export function ObservationReview({ observationId }: { observationId:string }) {
 const [context,setContext]=useState<Context|null>(null);const [error,setError]=useState('');const [revision,setRevision]=useState(0);const [busy,setBusy]=useState(false);
 useEffect(()=>{let active=true;setContext(null);api<Context>(`observations/${encodeURIComponent(observationId)}/review-context`).then(v=>{if(active)setContext(v);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[observationId,revision]);
 async function match(){setBusy(true);setError('');try{await api(`observations/${encodeURIComponent(observationId)}/match`,{});setRevision(v=>v+1);}catch(e){setError(String(e));}finally{setBusy(false);}}
 return <section><h4>设备关联复核</h4>{error&&<p role="alert">{error}</p>}<button disabled={busy} onClick={match}>按已记录的标记计算候选</button><button onClick={()=>setRevision(v=>v+1)}>刷新复核状态</button>
 {context&&<><p>米制估计和标记身份不能替代现场确认。共享密钥模式下，人员标签为自报信息。</p>
 {!context.matches.length&&<p>尚无匹配记录；先计算候选。无已记录标记的手工观测需经审核的结构化匹配输入。</p>}
 {context.matches.map(entry=><Decision key={`${entry.match.match_id}:${entry.latest_review?.review_id??''}`} entry={entry} scene={context.scene_version_id} saved={()=>setRevision(v=>v+1)}/>)}
 <details><summary>定位输入</summary><pre>{JSON.stringify(context.localizations,null,2)}</pre></details></>}
 </section>;
}
function Decision({entry,scene,saved}:{entry:Entry;scene:string|null;saved:()=>void}){
 const [asset,setAsset]=useState(entry.latest_review ? (entry.latest_review.asset_id??'') : (entry.match.asset_id??''));const [label,setLabel]=useState('');const [reason,setReason]=useState('');
 const [assets,setAssets]=useState<{asset_id:string;name:string}[]>([]);const [cursor,setCursor]=useState('');const [next,setNext]=useState<string|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 useEffect(()=>{let active=true;if(!scene)return;api<{items:{asset_id:string;name:string}[];next_cursor:string|null}>(`asset-page?scene_version_id=${encodeURIComponent(scene)}&cursor=${encodeURIComponent(cursor)}`).then(v=>{if(active){setAssets(v.items);setNext(v.next_cursor);}}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[scene,cursor]);
 async function save(){setBusy(true);setError('');try{await api('reviews',{review_id:crypto.randomUUID(),match_id:entry.match.match_id,asset_id:asset||null,operator_label:label.trim(),reason:reason.trim(),expected_review_id:entry.latest_review?.review_id??null});saved();}catch(e){setError(String(e));}finally{setBusy(false);}}
 return <article><p>{entry.match.match_id} · {entry.match.status} · 候选：{entry.match.candidate_asset_ids.join('、')||'无'}</p>
 {entry.latest_review&&<p>最新复核：{entry.latest_review.asset_id??'未确认任何设备'} · {entry.latest_review.operator_label} · {entry.latest_review.reason} · {entry.latest_review.reviewed_at}</p>}
 <label>复核决定 <select value={asset} onChange={e=>setAsset(e.target.value)}><option value="">拒绝关联 / 不确认设备</option>{asset&&!assets.some(a=>a.asset_id===asset)&&<option value={asset}>{asset}</option>}{assets.map(a=><option key={a.asset_id} value={a.asset_id}>{a.name} · {a.asset_id}</option>)}</select></label>
 <button onClick={()=>setCursor('')}>设备首页</button><button disabled={!next} onClick={()=>next&&setCursor(next)}>下一页设备</button><br/>
 <label>人员标签 <input value={label} maxLength={100} onChange={e=>setLabel(e.target.value)}/></label><label>依据与原因 <textarea value={reason} maxLength={1000} onChange={e=>setReason(e.target.value)}/></label>
 <button disabled={busy||!label.trim()||!reason.trim()} onClick={save}>保存追加复核</button>{error&&<p role="alert">{error}</p>}
 <p>选择设备表示人工确认关联；选择“不确认”会撤销本匹配的当前确认，历史记录仍保留。</p></article>;
}
