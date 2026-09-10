import { useEffect, useMemo, useState } from 'react';
import type { SensorDevice } from '@digital-twin/contracts';

type Asset = { asset_id: string; name: string; scene_version_id: string;
  position: { x: number; y: number; z: number }; units: Record<string, string> };

export function useRunAssets(sceneId: string | undefined, version: string | null | undefined) {
  const [result, setResult] = useState<{ version: string; assets: Asset[] } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setResult(null); setError(''); if (!version) return;
    const controller = new AbortController();
    fetch('/api/instruments/assets', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(`设备台账加载失败 (${response.status})`);
      return response.json() as Promise<Asset[]>;
    }).then(assets => setResult({ version, assets: assets.filter(a => a.scene_version_id === version) }))
      .catch(e => { if (!controller.signal.aborted) setError(String(e.message)); });
    return () => controller.abort();
  }, [version]);
  const devices = useMemo<SensorDevice[]>(() => result && result.version === version ? result.assets.map(asset => ({
    id: asset.asset_id, scene_id: sceneId ?? '', name: asset.name, type: 'delamination',
    unit: asset.units.deep_base ?? '', position: asset.position, status: 'offline',
    source_type: 'replay', provenance: { source: 'replay', status: 'pending_confirmation' },
  })) : [], [result, version, sceneId]);
  return { devices, error };
}

type Analysis = { status: string; recommendation: string; channels?: {
  name: string; value: number; unit: string; recommendation: string; threshold_status: string;
}[]; notice?: string };
const names: Record<string, string> = { deep_base: '深基点', shallow_base: '浅基点', delta: '差值' };
const labels: Record<string, string> = { normal: '正常', attention: '关注', review: '待复核', alarm: '报警建议',
  available: '有有效历史读数', missing: '无读数', future_only: '仅有未来读数', no_valid_prior_reading: '此前无有效读数' };
export function RunAssetEvidence({ assetId, at }: { assetId: string; at: string }) {
  const [data, setData] = useState<Analysis | null>(null); const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setData(null); setError('');
    fetch(`/api/instruments/assets/${encodeURIComponent(assetId)}/analysis?at=${encodeURIComponent(at)}`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`读数加载失败 (${response.status})`); return response.json() as Promise<Analysis>; })
      .then(setData).catch(e => { if (!controller.signal.aborted) setError(String(e.message)); });
    return () => controller.abort();
  }, [assetId, at]);
  return <div className="panel-body">
    <p>设备 {assetId} · 查询时刻 {at}</p>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p>正在读取…</p>}
    {data && <><p>{labels[data.status] ?? data.status} · {labels[data.recommendation] ?? data.recommendation}</p>
      {data.channels?.map(c => <p key={c.name}>{names[c.name] ?? c.name}：{c.value} {c.unit} · {labels[c.recommendation] ?? c.recommendation}</p>)}
      <p>{data.notice ?? '建议仅供人工复核，不替代工程判断。'}</p></>}
  </div>;
}
