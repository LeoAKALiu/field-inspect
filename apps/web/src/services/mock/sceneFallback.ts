/**
 * 演示数据（API 不可用时的本地兜底）。
 *
 * 所有空间坐标均派生自 assets/tunnel/liris/scene-metadata.json 的
 * recommendedRoute / recommendedDeviceAnchors / boundingBox —— 不在此处
 * 写死任何坐标常量；页面组件一律经 services 层获取，不直接引用本模块常量。
 *
 * 全部对象 provenance 标为 simulated，与契约 v2 的真实性边界一致。
 */
import type {
  DetectionEvent,
  PatrolTask,
  Position3D,
  SceneMetadata,
  SensorDevice,
  TrajectoryPoint,
  TwinTrajectoryPoint,
} from '@digital-twin/contracts';
import type { SceneMetadata as EngineSceneMetadata } from '@digital-twin/twin-viewer';
import { LIRIS_MESH_URL, LIRIS_POINTCLOUD_URL } from '../scene/engineMetadata';

const SIMULATED = { source: 'simulation', status: 'simulated' } as const;

/**
 * LIRIS 资产 scene-metadata.json 在契约 SceneMetadata 之外保留的原始字段
 * （engineMetadata 加载时原样保留，仅额外映射 scene_id / bounds_min / bounds_max）。
 */
interface LirisAssetExtras {
  sceneId?: string;
  boundingBox?: { min: Position3D; max: Position3D };
  recommendedRoute?: Position3D[];
  recommendedDeviceAnchors?: Array<{
    id?: string;
    label?: string;
    position: Position3D;
    note?: string;
  }>;
  transformDescription?: string;
  routeGeneration?: {
    method: 'pointcloud_auto_corridor_centerline';
    algorithmVersion: string;
    sourceAsset: string;
    sourceSha256: string;
    sourcePointCount: number;
    generatedPointCount: number;
    status: 'estimated' | 'measured';
    disclaimer: string;
  };
}

type LirisEngineMetadata = EngineSceneMetadata & LirisAssetExtras;

/** 沿路线弧长均匀采样 count 个点。 */
export function sampleRoute(route: readonly Position3D[], count: number): Position3D[] {
  if (route.length === 0) return [];
  if (route.length === 1 || count <= 1) return [{ ...route[0]! }];

  const segLengths: number[] = [];
  let total = 0;
  for (let i = 1; i < route.length; i++) {
    const a = route[i - 1]!;
    const b = route[i]!;
    const len = Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
    segLengths.push(len);
    total += len;
  }

  const points: Position3D[] = [];
  for (let k = 0; k < count; k++) {
    const target = (k / (count - 1)) * total;
    let acc = 0;
    let placed = false;
    for (let i = 0; i < segLengths.length; i++) {
      const seg = segLengths[i]!;
      if (target <= acc + seg || i === segLengths.length - 1) {
        const u = seg === 0 ? 0 : (target - acc) / seg;
        const a = route[i]!;
        const b = route[i + 1]!;
        points.push({
          x: a.x + (b.x - a.x) * u,
          y: a.y + (b.y - a.y) * u,
          z: a.z + (b.z - a.z) * u,
        });
        placed = true;
        break;
      }
      acc += seg;
    }
    if (!placed) points.push({ ...route[route.length - 1]! });
  }
  return points;
}

/** 两点连线航向角（度，绕 +Y 轴，0 = +X）。 */
export function headingBetween(from: Position3D, to: Position3D): number {
  const deg = (Math.atan2(to.z - from.z, to.x - from.x) * 180) / Math.PI;
  return deg < 0 ? deg + 360 : deg;
}

/** 契约 API 轨迹（ISO timestamp）→ 三维契约播放轨迹（相对秒）。 */
export function toTwinTrajectory(points: readonly TrajectoryPoint[]): TwinTrajectoryPoint[] {
  if (points.length === 0) return [];
  const t0 = Date.parse(points[0]!.timestamp);
  return points.map((p) => {
    const t = Date.parse(p.timestamp);
    return {
      time: Number.isNaN(t0) || Number.isNaN(t) ? p.seq : (t - t0) / 1000,
      position: { ...p.position },
      heading_deg: p.heading_deg ?? undefined,
    };
  });
}

/** 找距给定位置最近的轨迹点，返回其播放时刻（秒）；用于事件 → 回放时刻联动。 */
export function nearestTrajectoryTime(
  trajectory: readonly TrajectoryPoint[],
  position: Position3D,
): number {
  if (trajectory.length === 0) return 0;
  let best = trajectory[0]!;
  let bestDist = Infinity;
  for (const p of trajectory) {
    const d =
      (p.position.x - position.x) ** 2 +
      (p.position.y - position.y) ** 2 +
      (p.position.z - position.z) ** 2;
    if (d < bestDist) {
      bestDist = d;
      best = p;
    }
  }
  const t0 = Date.parse(trajectory[0]!.timestamp);
  const t = Date.parse(best.timestamp);
  return Number.isNaN(t0) || Number.isNaN(t) ? best.seq : (t - t0) / 1000;
}

/** 路线总长度（米）。 */
export function routeLength(route: readonly Position3D[]): number {
  let total = 0;
  for (let i = 1; i < route.length; i++) {
    const a = route[i - 1]!;
    const b = route[i]!;
    total += Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z);
  }
  return total;
}

/** 由巡检路线派生演示轨迹（时间步长固定 2s）。 */
export function deriveTrajectoryFromRoute(
  route: readonly Position3D[],
  taskId: string,
  pointCount = 60,
  stepSeconds = 2,
): TrajectoryPoint[] {
  const samples = sampleRoute(route, pointCount);
  const base = Date.parse('2026-08-05T14:00:00.000Z');
  return samples.map((position, i) => {
    const next = samples[Math.min(i + 1, samples.length - 1)]!;
    return {
      task_id: taskId,
      seq: i,
      timestamp: new Date(base + i * stepSeconds * 1000).toISOString(),
      position,
      heading_deg: headingBetween(position, next),
      speed_mps: 4.2,
      source_type: 'simulation',
      provenance: SIMULATED,
    };
  });
}

/** 轨迹在 t 秒处的采样（线性插值），供页面车辆状态展示使用。 */
export function sampleTrajectoryAt(
  trajectory: readonly TrajectoryPoint[],
  t: number,
): TrajectoryPoint | null {
  if (trajectory.length === 0) return null;
  const t0 = Date.parse(trajectory[0]!.timestamp);
  const seconds = trajectory.map((p) => (Date.parse(p.timestamp) - t0) / 1000);
  const clamped = Math.max(seconds[0]!, Math.min(t, seconds[seconds.length - 1]!));
  let i1 = seconds.findIndex((s) => s >= clamped);
  if (i1 <= 0) i1 = 1;
  const i0 = i1 - 1;
  const a = trajectory[Math.min(i0, trajectory.length - 1)]!;
  const b = trajectory[Math.min(i1, trajectory.length - 1)]!;
  const span = seconds[Math.min(i1, seconds.length - 1)]! - seconds[i0]!;
  const u = span === 0 ? 0 : (clamped - seconds[i0]!) / span;
  return {
    ...b,
    position: {
      x: a.position.x + (b.position.x - a.position.x) * u,
      y: a.position.y + (b.position.y - a.position.y) * u,
      z: a.position.z + (b.position.z - a.position.z) * u,
    },
  };
}

/** 契约 SceneMetadata 兜底：由引擎资产元数据换算。 */
export function buildFallbackSceneMetadata(engine: LirisEngineMetadata): SceneMetadata {
  const route = engine.recommendedRoute ?? [];
  return {
    scene_id: engine.sceneId ?? 'liris-tunnel',
    name: engine.name ?? 'LIRIS 隧道场景',
    coordinate_system: 'scene_local_yup',
    units: 'm',
    up_axis: 'Y',
    bounds_min: engine.boundingBox?.min ?? { x: 0, y: 0, z: 0 },
    bounds_max: engine.boundingBox?.max ?? { x: 0, y: 0, z: 0 },
    length_m: Math.round(routeLength(route)),
    mesh_url: LIRIS_MESH_URL,
    pointcloud_url: LIRIS_POINTCLOUD_URL,
    route,
    route_provenance: engine.routeGeneration
      ? {
          method: engine.routeGeneration.method,
          algorithm_version: engine.routeGeneration.algorithmVersion,
          source_asset: engine.routeGeneration.sourceAsset,
          source_sha256: engine.routeGeneration.sourceSha256,
          source_point_count: engine.routeGeneration.sourcePointCount,
          generated_point_count: engine.routeGeneration.generatedPointCount,
          status: engine.routeGeneration.status,
          disclaimer: engine.routeGeneration.disclaimer,
        }
      : undefined,
    description: engine.transformDescription,
    source_type: 'simulation',
    provenance: SIMULATED,
  };
}

const FALLBACK_DEVICE_TYPES: Array<{ type: SensorDevice['type']; name: string; unit: string }> = [
  { type: 'delamination', name: '顶板离层仪', unit: 'mm' },
  { type: 'displacement', name: '侧墙位移计', unit: 'mm' },
  { type: 'convergence', name: '拱收敛计', unit: 'mm/d' },
  { type: 'stress', name: '衬砌应力计', unit: 'MPa' },
  { type: 'delamination', name: '拱顶离层仪', unit: 'mm' },
  { type: 'displacement', name: '围岩位移计', unit: 'mm' },
];

/** 演示监测点位：锚点位置来自场景元数据 recommendedDeviceAnchors。 */
export function buildFallbackDevices(engine: LirisEngineMetadata): SensorDevice[] {
  const anchors = engine.recommendedDeviceAnchors ?? [];
  return FALLBACK_DEVICE_TYPES.map((spec, i) => {
    const anchor = anchors[i % Math.max(anchors.length, 1)];
    return {
      id: `DEV-${spec.type.toUpperCase()}-${(i + 1).toString().padStart(2, '0')}`,
      scene_id: engine.sceneId ?? 'liris-tunnel',
      name: `${spec.name} #${(i + 1).toString().padStart(2, '0')}`,
      type: spec.type,
      unit: spec.unit,
      position: anchor ? { ...anchor.position } : undefined,
      status: i === FALLBACK_DEVICE_TYPES.length - 1 ? 'offline' : 'online',
      source_type: 'simulation',
      provenance: SIMULATED,
    };
  });
}

const FALLBACK_EVENT_SPECS: Array<{
  type: DetectionEvent['type'];
  severity: DetectionEvent['severity'];
  status: DetectionEvent['status'];
  description: string;
  confidence: number;
  routeIndex: number;
  lateralOffset: number;
}> = [
  { type: 'spalling', severity: 'medium', status: 'acknowledged', description: '拱顶表层混凝土剥落迹象', confidence: 0.94, routeIndex: 2, lateralOffset: 2.4 },
  { type: 'crack', severity: 'high', status: 'open', description: '左侧墙衬砌裂缝扩展趋势', confidence: 0.89, routeIndex: 5, lateralOffset: -2.8 },
  { type: 'water_leakage', severity: 'low', status: 'open', description: '拱腰施工缝微量渗水痕迹', confidence: 0.78, routeIndex: 9, lateralOffset: 1.6 },
  { type: 'corrosion', severity: 'low', status: 'false_positive', description: '右墙缆线槽盖板锈蚀痕迹', confidence: 0.62, routeIndex: 12, lateralOffset: -2.2 },
];

/** 演示异常候选：位置由巡检路线采样点加侧向偏移得到。 */
export function buildFallbackEvents(engine: LirisEngineMetadata, taskId: string): DetectionEvent[] {
  const route = engine.recommendedRoute ?? [];
  return FALLBACK_EVENT_SPECS.map((spec, i) => {
    const anchor = route[Math.min(spec.routeIndex, Math.max(route.length - 1, 0))];
    const position: Position3D = anchor
      ? { x: anchor.x, y: anchor.y, z: anchor.z + spec.lateralOffset }
      : { x: 0, y: 0, z: 0 };
    return {
      id: `EVT-2026-${(i + 1).toString().padStart(3, '0')}`,
      scene_id: engine.sceneId ?? 'liris-tunnel',
      task_id: taskId,
      type: spec.type,
      severity: spec.severity,
      status: spec.status,
      position,
      description: spec.description,
      confidence: spec.confidence,
      detected_at: '2026-08-05T14:2' + i + ':00.000Z',
      handled_by: spec.status === 'open' ? null : '李工（监测组）',
      source_type: 'simulation',
      provenance: SIMULATED,
    };
  });
}

/** 演示巡检任务。 */
export function buildFallbackTasks(engine: LirisEngineMetadata): PatrolTask[] {
  const length = Math.round(routeLength(engine.recommendedRoute ?? []));
  const sceneId = engine.sceneId ?? 'liris-tunnel';
  return [
    {
      id: 'TSK-20260805-01',
      scene_id: sceneId,
      name: '隧道全断面激光点云综合巡检',
      mode: 'simulation',
      status: 'completed',
      planned_start: '2026-08-05T14:00:00.000Z',
      actual_start: '2026-08-05T14:00:00.000Z',
      actual_end: '2026-08-05T14:45:00.000Z',
      distance_m: length,
      event_count: FALLBACK_EVENT_SPECS.length,
      run_kind: 'demonstration',
      package_status: null,
      acceptance_state: 'not_applicable',
      scene_version_id: null,
      alignment_id: null,
      has_pointcloud: false,
      acceptance_recorded_at: null,
      acceptance_summary: null,
      source_type: 'simulation',
      provenance: SIMULATED,
    },
    {
      id: 'TSK-20260801-02',
      scene_id: sceneId,
      name: '结构离层专项复测',
      mode: 'replay',
      status: 'completed',
      planned_start: '2026-08-01T10:15:00.000Z',
      actual_start: '2026-08-01T10:15:00.000Z',
      actual_end: '2026-08-01T10:35:00.000Z',
      distance_m: Math.round(length / 2),
      event_count: 1,
      run_kind: 'demonstration',
      package_status: null,
      acceptance_state: 'not_applicable',
      scene_version_id: null,
      alignment_id: null,
      has_pointcloud: false,
      acceptance_recorded_at: null,
      acceptance_summary: null,
      source_type: 'replay',
      provenance: SIMULATED,
    },
  ];
}
