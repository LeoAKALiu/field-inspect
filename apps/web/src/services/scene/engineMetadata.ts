import { useEffect, useState } from 'react';
import type { SceneMetadata as EngineSceneMetadata } from '@digital-twin/twin-viewer';

export type PackagedEngineSceneMetadata = EngineSceneMetadata & {
  /** Row-major 4×4 raw asset → scene_local_yup transform. */
  transformMatrix: number[];
};

/**
 * 引擎级场景元数据（assets/tunnel/liris/scene-metadata.json）：
 * 包含 transformMatrix、推荐相机与推荐路线，由 twin-viewer 直接消费。
 * 该文件是本地静态资产（经 vite publicDir 挂载），离线必定可用。
 */
export const ENGINE_METADATA_URL = '/tunnel/liris/scene-metadata.json';

export const LIRIS_MESH_URL = '/tunnel/liris/tunnel_mesh.obj';
export const LIRIS_POINTCLOUD_URL = '/tunnel/liris/tunnel_pointcloud.ply';

let cache: Promise<PackagedEngineSceneMetadata> | null = null;

export function loadEngineSceneMetadata(): Promise<PackagedEngineSceneMetadata> {
  cache ??= fetch(ENGINE_METADATA_URL).then(async (res) => {
    if (!res.ok) throw new Error(`scene-metadata 加载失败（${res.status}）`);
    const raw = (await res.json()) as Record<string, unknown> & {
      sceneId?: string;
      boundingBox?: { min: unknown; max: unknown };
      transformMatrix?: unknown;
    };
    if (
      !Array.isArray(raw.transformMatrix) ||
      raw.transformMatrix.length !== 16 ||
      !raw.transformMatrix.every((value) => typeof value === 'number')
    ) {
      throw new Error('scene-metadata transformMatrix 必须是 16 个数值的 row-major 4×4 矩阵');
    }
    /* 引擎（twin-viewer 新契约）按契约 v2 读取 scene_id / bounds_min / bounds_max，
     * 而 LIRIS 资产 JSON 仍是原始命名（sceneId / boundingBox.min|max）。
     * 在应用侧边界做一次字段映射；旧字段原样保留（sceneFallback 仍消费
     * recommendedRoute / recommendedDeviceAnchors / boundingBox 等）。 */
    return {
      ...raw,
      scene_id: raw.sceneId,
      bounds_min: raw.boundingBox?.min,
      bounds_max: raw.boundingBox?.max,
      transformMatrix: raw.transformMatrix,
    } as unknown as PackagedEngineSceneMetadata;
  });
  return cache;
}

export function useEngineSceneMetadata(): {
  data: PackagedEngineSceneMetadata | null;
  loading: boolean;
  error: string | null;
} {
  const [state, setState] = useState<{
    data: PackagedEngineSceneMetadata | null;
    loading: boolean;
    error: string | null;
  }>({ data: null, loading: true, error: null });

  useEffect(() => {
    let cancelled = false;
    loadEngineSceneMetadata()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            data: null,
            loading: false,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
