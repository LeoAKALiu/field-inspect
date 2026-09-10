import * as THREE from 'three';
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js';
import { rowMajorToThreeMatrix } from '../math/matrix.js';

export interface PointCloudLoadOptions {
  transformMatrix: readonly number[];
  pointSize?: number;
  vertexColors?: boolean;
  fallbackColor?: number;
  onProgress?: (loaded: number, total: number) => void;
}

/**
 * Load PLY point cloud (~16MB LIRIS file is fine for Three.js Points).
 * Transform is applied to geometry; source file is not modified.
 */
export async function loadPlyPointCloud(
  url: string,
  options: PointCloudLoadOptions,
): Promise<THREE.Points> {
  const loader = new PLYLoader();
  const geometry = await new Promise<THREE.BufferGeometry>((resolve, reject) => {
    loader.load(
      url,
      (geo) => resolve(geo),
      (ev) => {
        if (ev.total > 0) options.onProgress?.(ev.loaded, ev.total);
      },
      (err) => reject(err instanceof Error ? err : new Error(String(err))),
    );
  });

  const matrix = rowMajorToThreeMatrix(options.transformMatrix);
  geometry.applyMatrix4(matrix);
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();

  const hasColor = !!geometry.getAttribute('color');
  const useVertex = (options.vertexColors ?? true) && hasColor;
  const material = new THREE.PointsMaterial({
    size: options.pointSize ?? 0.12,
    sizeAttenuation: true,
    vertexColors: useVertex,
    color: useVertex ? 0xffffff : (options.fallbackColor ?? 0xb8c4d0),
  });

  const points = new THREE.Points(geometry, material);
  points.name = 'tunnel-pointcloud';
  points.frustumCulled = true;
  return points;
}

export function setPointSize(points: THREE.Points, size: number): void {
  const mat = points.material;
  if (mat instanceof THREE.PointsMaterial) {
    mat.size = size;
    mat.needsUpdate = true;
  }
}
