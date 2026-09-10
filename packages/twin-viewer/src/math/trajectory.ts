import type { Position3D, TwinTrajectoryPoint } from '@digital-twin/contracts';
import { clamp, lerpAngleDeg, lerpPosition } from './matrix.js';

export interface SampledVehiclePose {
  position: Position3D;
  heading_deg: number;
  time: number;
  segmentIndex: number;
}

/** Playback times are already seconds relative to task start (contracts TwinTrajectoryPoint.time). */
export function trajectoryTimesSeconds(points: readonly TwinTrajectoryPoint[]): number[] {
  return points.map((p) => p.time);
}

/**
 * Sample trajectory at playbackTime (seconds from task start).
 * Positions must already be scene_local_yup meters.
 */
export function sampleTrajectory(
  points: readonly TwinTrajectoryPoint[],
  playbackTime: number,
): SampledVehiclePose | null {
  if (points.length === 0) return null;
  const times = trajectoryTimesSeconds(points);
  if (points.length === 1) {
    const p = points[0]!;
    return {
      position: { ...p.position },
      heading_deg: p.heading_deg ?? 0,
      time: times[0]!,
      segmentIndex: 0,
    };
  }

  const tMin = times[0]!;
  const tMax = times[times.length - 1]!;
  const t = clamp(playbackTime, Math.min(tMin, tMax), Math.max(tMin, tMax));

  let i1 = 1;
  while (i1 < times.length && times[i1]! < t) i1 += 1;
  if (i1 >= times.length) i1 = times.length - 1;
  const i0 = Math.max(0, i1 - 1);
  const a = points[i0]!;
  const b = points[i1]!;
  const ta = times[i0]!;
  const tb = times[i1]!;
  const u = tb === ta ? 0 : (t - ta) / (tb - ta);

  const ha = a.heading_deg ?? headingFromSegment(a.position, b.position);
  const hb = b.heading_deg ?? headingFromSegment(a.position, b.position);

  return {
    position: lerpPosition(a.position, b.position, u),
    heading_deg: lerpAngleDeg(ha, hb, u),
    time: t,
    segmentIndex: i0,
  };
}

export function headingFromSegment(from: Position3D, to: Position3D): number {
  const dx = to.x - from.x;
  const dz = to.z - from.z;
  let deg = (Math.atan2(dz, dx) * 180) / Math.PI;
  if (deg < 0) deg += 360;
  return deg;
}

export function splitTrajectoryByTime(
  points: readonly TwinTrajectoryPoint[],
  playbackTime: number,
): { past: Position3D[]; future: Position3D[] } {
  if (points.length === 0) return { past: [], future: [] };
  const sample = sampleTrajectory(points, playbackTime);
  if (!sample) return { past: [], future: [] };

  const past: Position3D[] = [];
  const future: Position3D[] = [];
  const times = trajectoryTimesSeconds(points);

  for (let i = 0; i < points.length; i++) {
    const p = { ...points[i]!.position };
    if (times[i]! <= sample.time) past.push(p);
    if (times[i]! >= sample.time) future.push(p);
  }

  if (past.length === 0 || !samePos(past[past.length - 1]!, sample.position)) {
    past.push({ ...sample.position });
  } else {
    past[past.length - 1] = { ...sample.position };
  }
  if (future.length === 0 || !samePos(future[0]!, sample.position)) {
    future.unshift({ ...sample.position });
  } else {
    future[0] = { ...sample.position };
  }

  return { past, future };
}

function samePos(a: Position3D, b: Position3D): boolean {
  return a.x === b.x && a.y === b.y && a.z === b.z;
}
