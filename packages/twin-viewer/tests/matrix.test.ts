import { describe, expect, it } from 'vitest';
import { applyMatrix4RowMajor, headingDegToQuaternion } from '../src/math/matrix.js';

describe('applyMatrix4RowMajor', () => {
  it('maps Blender Z-up sample through documented LIRIS matrix form', () => {
    // Identity
    const I = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    expect(applyMatrix4RowMajor(I, { x: 1, y: 2, z: 3 })).toEqual({ x: 1, y: 2, z: 3 });
  });

  it('applies Z-up→Y-up rotation (x,y,z)→(x,z,-y)', () => {
    const R = [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1];
    expect(applyMatrix4RowMajor(R, { x: 1, y: 2, z: 3 })).toEqual({ x: 1, y: 3, z: -2 });
  });

  it('throws on wrong length', () => {
    expect(() => applyMatrix4RowMajor([1, 2, 3], { x: 0, y: 0, z: 0 })).toThrow(/16/);
  });
});

describe('headingDegToQuaternion', () => {
  it('returns unit quaternion', () => {
    const q = headingDegToQuaternion(90);
    expect(q.length()).toBeCloseTo(1, 5);
  });
});
