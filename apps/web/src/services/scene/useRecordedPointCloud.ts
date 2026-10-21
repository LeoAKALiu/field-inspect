import { useCallback, useEffect, useState } from 'react';
import type { ErrorResponse } from '@digital-twin/contracts';

type Preview = { runId: string; url: string | null; loading: boolean; error: string; notice: string };
const errorReasons: Record<string, string> = {
  pointcloud_not_found: '当前运行没有可展示的点云。',
  pointcloud_binding_mismatch: '点云与运行、场景对齐或分片索引不一致。',
  pointcloud_unsafe_path: '点云归档含不安全路径、链接或特殊文件。',
  pointcloud_size_limit: '点云超过预览大小、点数或分片数量限制。',
  pointcloud_digest_mismatch: '点云文件摘要与导入记录不一致。',
  pointcloud_invalid_format: '点云格式或元数据不受支持、损坏或截断。',
  pointcloud_invalid_coordinates: '点云坐标含非有限值或超出有效范围。',
  pointcloud_count_mismatch: '点云实际点数与分片索引不一致。',
  pointcloud_file_missing: '点云来源文件缺失，请检查归档。',
  pointcloud_file_unavailable: '点云来源文件无法读取，请检查文件访问权限。',
};

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
          const body = await response.json().catch(() => null) as ErrorResponse | null;
          const code = body?.error?.code;
          if (code && errorReasons[code]) {
            throw new Error(`点云读取失败 (${response.status})，${errorReasons[code]} ${body?.error?.message ?? ''}`);
          }
          const reason = response.status === 503 ? '服务器点云依赖未就绪，请先安装项目锁定依赖。'
            : response.status === 409 ? '导入正在占用归档，请稍后重试。'
              : response.status === 401 || response.status === 403 ? '访问未授权，请重新核对访问身份。'
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
