import * as THREE from 'three';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { rowMajorToThreeMatrix } from '../math/matrix.js';

export interface MeshLoadOptions {
  /** Row-major 4×4 source→viewer. */
  transformMatrix: readonly number[];
  opacity?: number;
  onProgress?: (loaded: number, total: number) => void;
}

/**
 * Load OBJ mesh. Original file coordinates are transformed via matrix —
 * the file on disk is never rewritten.
 */
export async function loadObjMesh(
  url: string,
  options: MeshLoadOptions,
): Promise<THREE.Group> {
  const loader = new OBJLoader();
  const group = await new Promise<THREE.Group>((resolve, reject) => {
    loader.load(
      url,
      (obj) => resolve(obj),
      (ev) => {
        if (ev.total > 0) options.onProgress?.(ev.loaded, ev.total);
      },
      (err) => reject(err instanceof Error ? err : new Error(String(err))),
    );
  });

  const matrix = rowMajorToThreeMatrix(options.transformMatrix);
  group.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      child.geometry = child.geometry.clone();
      child.geometry.applyMatrix4(matrix);
      child.geometry.computeVertexNormals();
      child.geometry.computeBoundingBox();
      child.geometry.computeBoundingSphere();
      const opacity = options.opacity ?? 1;
      child.material = new THREE.MeshStandardMaterial({
        color: 0x9aa3ad,
        roughness: 0.92,
        metalness: 0.05,
        transparent: opacity < 1,
        opacity,
        side: THREE.DoubleSide,
      });
      child.castShadow = false;
      child.receiveShadow = false;
      child.name = child.name || 'tunnel-mesh-part';
    }
  });

  group.name = 'tunnel-mesh';
  group.updateMatrixWorld(true);
  return group;
}

export function setMeshOpacity(root: THREE.Object3D, opacity: number): void {
  const o = Math.min(1, Math.max(0, opacity));
  root.traverse((child) => {
    if (child instanceof THREE.Mesh && child.material instanceof THREE.MeshStandardMaterial) {
      child.material.opacity = o;
      child.material.transparent = o < 1;
      child.material.depthWrite = o >= 0.99;
      child.material.needsUpdate = true;
    }
  });
}
