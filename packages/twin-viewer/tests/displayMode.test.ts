import { describe, expect, it } from 'vitest';

/** Pure visibility policy for mesh | pointcloud | overlay (no reload). */
function visibility(mode: 'mesh' | 'pointcloud' | 'overlay') {
  return {
    mesh: mode === 'mesh' || mode === 'overlay',
    pointcloud: mode === 'pointcloud' || mode === 'overlay',
    entities: true,
  };
}

describe('display mode visibility', () => {
  it('mesh only', () => {
    expect(visibility('mesh')).toEqual({ mesh: true, pointcloud: false, entities: true });
  });
  it('pointcloud only', () => {
    expect(visibility('pointcloud')).toEqual({ mesh: false, pointcloud: true, entities: true });
  });
  it('overlay both', () => {
    expect(visibility('overlay')).toEqual({ mesh: true, pointcloud: true, entities: true });
  });
});
