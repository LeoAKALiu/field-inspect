import { describe, expect, it } from 'vitest';
import {
  sampleTrajectory,
  trajectoryTimesSeconds,
  splitTrajectoryByTime,
} from '../src/math/trajectory.js';
import type { TwinTrajectoryPoint } from '@digital-twin/contracts';

const points: TwinTrajectoryPoint[] = [
  { time: 0, position: { x: 0, y: 0, z: 0 }, heading_deg: 0 },
  { time: 10, position: { x: 10, y: 0, z: 0 }, heading_deg: 0 },
  { time: 20, position: { x: 10, y: 0, z: 10 }, heading_deg: 90 },
];

describe('trajectoryTimesSeconds (TwinTrajectoryPoint.time)', () => {
  it('reads time field directly', () => {
    expect(trajectoryTimesSeconds(points)).toEqual([0, 10, 20]);
  });
});

describe('sampleTrajectory', () => {
  it('interpolates mid segment', () => {
    const s = sampleTrajectory(points, 5);
    expect(s!.position.x).toBeCloseTo(5, 5);
    expect(s!.heading_deg).toBeCloseTo(0, 5);
  });

  it('clamps outside range', () => {
    expect(sampleTrajectory(points, -5)!.position.x).toBeCloseTo(0, 5);
    expect(sampleTrajectory(points, 999)!.position.z).toBeCloseTo(10, 5);
  });
});

describe('splitTrajectoryByTime', () => {
  it('splits past/future with continuity at current pose', () => {
    const { past, future } = splitTrajectoryByTime(points, 10);
    expect(past.length).toBeGreaterThanOrEqual(2);
    expect(future.length).toBeGreaterThanOrEqual(2);
    expect(past[past.length - 1]!.x).toBeCloseTo(10, 5);
    expect(future[0]!.x).toBeCloseTo(10, 5);
  });
});
