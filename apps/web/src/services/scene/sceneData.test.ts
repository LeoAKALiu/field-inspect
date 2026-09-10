import { describe, expect, it } from 'vitest';
import { mayFillTrajectoryFromSceneRoute } from './sceneData';

describe('mayFillTrajectoryFromSceneRoute', () => {
  it('never fills a Recorded Run from a scene route', () => {
    expect(mayFillTrajectoryFromSceneRoute('recorded')).toBe(false);
  });

  it('allows demonstration and unknown tasks to use a scene route', () => {
    expect(mayFillTrajectoryFromSceneRoute('demonstration')).toBe(true);
    expect(mayFillTrajectoryFromSceneRoute(undefined)).toBe(true);
  });
});
