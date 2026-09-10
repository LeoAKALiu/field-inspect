import type { Position3D, SceneMetadata } from '@digital-twin/contracts';

export interface SceneBoundsInfo {
  min: Position3D;
  max: Position3D;
  center: Position3D;
  diagonal: number;
  near: number;
  far: number;
}

export function sceneBoundsFromMetadata(meta: SceneMetadata): SceneBoundsInfo {
  const min = meta.bounds_min;
  const max = meta.bounds_max;
  const center: Position3D = {
    x: (min.x + max.x) / 2,
    y: (min.y + max.y) / 2,
    z: (min.z + max.z) / 2,
  };
  const diagonal = Math.hypot(max.x - min.x, max.y - min.y, max.z - min.z) || 1;
  return {
    min,
    max,
    center,
    diagonal,
    near: Math.max(0.1, diagonal / 500),
    far: Math.max(100, diagonal * 4),
  };
}
