import { useEffect, useState, type ReactNode } from 'react';
export function PagedRecordList({ kind, revision, children }: { kind: string; revision: number; children: (row: Record<string,unknown>) => ReactNode }) {
  const [stack,setStack] = useState(['']); const [next,setNext] = useState<string|null>(null);
  const [rows,setRows] = useState<Record<string,unknown>[]>([]); const [error,setError] = useState(''); const [loading,setLoading] = useState(false);
  const cursor = stack[stack.length-1] ?? '';
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(''); setRows([]); setNext(null);
    fetch(`/api/instruments/pages/${kind}?cursor=${encodeURIComponent(cursor)}&limit=20`,{signal:controller.signal})
      .then(async r => { const v=await r.json(); if(!r.ok) throw new Error(v.error?.message ?? '读取失败'); return v; })
      .then(page => { if(!controller.signal.aborted){setRows(page.items);setNext(page.next_cursor);} })
      .catch(e=>{if(!controller.signal.aborted)setError(String(e.message));}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[kind,cursor,revision]);
  return <><button onClick={()=>setStack([''])}>回首页</button><button disabled={loading||stack.length===1} onClick={()=>setStack(v=>v.slice(0,-1))}>上一页</button>
    <span>第 {stack.length} 页</span><button disabled={loading||!next} onClick={()=>next&&setStack(v=>[...v,next])}>下一页</button>
    {error&&<p role="alert">{error}</p>}{loading?<p>正在读取…</p>:rows.length?rows.map(children):<p>本页暂无记录。</p>}</>;
}
