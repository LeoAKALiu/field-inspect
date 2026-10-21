import { useCallback, useEffect, useState } from 'react';

type Preview = { runId: string; url: string | null; loading: boolean; error: string; notice: string };

export function useRecordedPointCloud(runId: string | null) {
  const [state, setState] = useState<Preview | null>(null);
  const [revision, setRevision] = useState(0);
  const retry = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (!runId) { setState(null); return; }
    const identity = runId;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setState({ runId, url: null, loading: true, error: '', notice: '' });
    async function load() {
      try {
        const response = await fetch(`/api/tasks/${encodeURIComponent(identity)}/pointcloud.ply`, { signal: controller.signal });
        if (!response.ok) {
          const reason = response.status === 503 ? '服务器点云依赖未就绪，请先安装项目锁定依赖。'
            : response.status === 409 ? '导入正在占用归档，请稍后重试。'
              : '请核对访问授权、归档完整性和预览格式/大小限制。';
          throw new Error(`点云读取失败 (${response.status})，${reason}`);
        }
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        const source = response.headers.get('X-Pointcloud-Source-Points');
        const shown = response.headers.get('X-Pointcloud-Preview-Points');
        const chunks = response.headers.get('X-Pointcloud-Chunks');
        setState({ runId: identity, url: objectUrl, loading: false, error: '',
          notice: `本运行点云预览：${chunks} 份来源，显示 ${shown} / ${source} 点；完整运行静态叠加，原始归档保留。` });
      } catch (error) {
        if (!controller.signal.aborted) setState({ runId: identity, url: null, loading: false,
          error: error instanceof Error ? error.message : String(error), notice: '' });
      }
    }
    void load();
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [runId, revision]);

  return { ...(state?.runId === runId ? state : null), retry };
}
