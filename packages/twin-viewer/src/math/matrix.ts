import * as THREE from 'three';
import type { Position3D } from '@digital-twin/contracts';

export const IDENTITY_MATRIX_4: readonly number[] = [
  1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
];

/** Apply row-major 4×4 matrix to a point (w=1). */
export function applyMatrix4RowMajor(m: readonly number[], p: Position3D): Position3D {
  if (m.length !== 16) {
    throw new Error(`transformMatrix must have 16 elements, got ${m.length}`);
  }
  const x = p.x;
  const y = p.y;
  const z = p.z;
  return {
    x: m[0]! * x + m[1]! * y + m[2]! * z + m[3]!,
    y: m[4]! * x + m[5]! * y + m[6]! * z + m[7]!,
    z: m[8]! * x + m[9]! * y + m[10]! * z + m[11]!,
  };
}

/** Three.js Matrix4 is column-major; convert from row-major flat array. */
export function rowMajorToThreeMatrix(m: readonly number[]): THREE.Matrix4 {
  if (m.length !== 16) {
    throw new Error(`transformMatrix must have 16 elements, got ${m.length}`);
  }
  const mat = new THREE.Matrix4();
  mat.set(
    m[0]!, m[1]!, m[2]!, m[3]!,
    m[4]!, m[5]!, m[6]!, m[7]!,
    m[8]!, m[9]!, m[10]!, m[11]!,
    m[12]!, m[13]!, m[14]!, m[15]!,
  );
  return mat;
}

export function positionToVector3(p: Position3D, out = new THREE.Vector3()): THREE.Vector3 {
  return out.set(p.x, p.y, p.z);
}

export function vector3ToPosition(v: THREE.Vector3): Position3D {
  return { x: v.x, y: v.y, z: v.z };
}

/** Heading degrees (0 = +X, CCW about +Y) → quaternion in Y-up frame. */
export function headingDegToQuaternion(
  headingDeg: number,
  out = new THREE.Quaternion(),
): THREE.Quaternion {
  const yaw = THREE.MathUtils.degToRad(headingDeg);
  return out.setFromAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
}

export function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v));
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function lerpPosition(a: Position3D, b: Position3D, t: number): Position3D {
  return {
    x: lerp(a.x, b.x, t),
    y: lerp(a.y, b.y, t),
    z: lerp(a.z, b.z, t),
  };
}

export function lerpAngleDeg(a: number, b: number, t: number): number {
  const d = ((b - a + 540) % 360) - 180;
  return (a + d * t + 360) % 360;
}
