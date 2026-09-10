import { describe, expect, it } from 'vitest';
import { headingFromSegment } from '../src/math/trajectory.js';
import { lerpAngleDeg, positionToVector3, vector3ToPosition } from '../src/math/matrix.js';
import * as THREE from 'three';

describe('Y-up coordinate helpers', () => {
  it('round-trips Position3D ↔ Vector3', () => {
    const p = { x: 1.5, y: 2.5, z: -3 };
    const v = positionToVector3(p);
    expect(vector3ToPosition(v)).toEqual(p);
    expect(v).toBeInstanceOf(THREE.Vector3);
  });

  it('headingFromSegment uses XZ plane (Y-up)', () => {
    expect(headingFromSegment({ x: 0, y: 0, z: 0 }, { x: 1, y: 9, z: 0 })).toBeCloseTo(0, 5);
    expect(headingFromSegment({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 1 })).toBeCloseTo(90, 5);
  });

  it('lerpAngleDeg shortest path', () => {
    expect(lerpAngleDeg(350, 10, 0.5)).toBeCloseTo(0, 5);
  });
});
