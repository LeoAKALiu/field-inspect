import { useEffect, useState } from 'react';
import type {
  DetectionEvent,
  DisplayTrajectory,
  PatrolTask,
  SceneMetadata,
  SensorDevice,
  TrajectoryPoint,
} from '@digital-twin/contracts';
import { api } from '../api/client';
import { loadEngineSceneMetadata } from './engineMetadata';
import {
  buildFallbackDevices,
  buildFallbackEvents,
  buildFallbackSceneMetadata,
  buildFallbackTasks,
  deriveTrajectoryFromRoute,
} from '../mock/sceneFallback';

/**
 * 场景聚合数据：车辆轨迹、设备点位、异常候选、场景元数据。
 * 优先取后端 API（契约 v2），任一环节不可用则整体降级为
 * 由 LIRIS 场景路线派生的本地演示数据（provenance = simulated）。
 * 页面组件只消费本 hook，不直接引用任何坐标常量。
 */
export interface SceneData {
  metadata: SceneMetadata;
  devices: SensorDevice[];
  events: DetectionEvent[];
  tasks: PatrolTask[];
  /** 主任务轨迹（页面车辆位姿/回放由此派生）。 */
  trajectory: TrajectoryPoint[];
  /** 数据来源：api = 后端契约数据；fallback = 本地演示数据。 */
  dataOrigin: 'api' | 'fallback';
}

interface SceneDataState {
  data: SceneData | null;
  loading: boolean;
}

export function useSceneData(): SceneDataState {
  const [state, setState] = useState<SceneDataState>({ data: null, loading: true });

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        // 引擎元数据是本地静态资产，离线可用；兜底数据也由其派生。
        const engine = await loadEngineSceneMetadata();

        try {
        const scenes = await api.listScenes();
        // 列表按 created_at 升序，归档场景可能排在最前；优先取 active 场景。
        const scene = scenes.find((s) => s.status === 'active') ?? scenes[0];
        if (!scene) throw new Error('no scene available');

        const [metadata, devices, events, tasks] = await Promise.all([
          api.getSceneMetadata(scene.id),
          api.listDevices({ scene_id: scene.id }),
          api.listEvents({ scene_id: scene.id }),
          api.listTasks({ scene_id: scene.id }),
        ]);

        const primaryTask = tasks[0] ?? null;
        let trajectory: TrajectoryPoint[] = [];
        if (primaryTask) {
          trajectory = await api.getTaskReplayTrajectory(primaryTask.id).catch(() => []);
        }
        if (
          trajectory.length === 0 &&
          primaryTask &&
          mayFillTrajectoryFromSceneRoute(primaryTask.run_kind)
        ) {
          trajectory = deriveTrajectoryFromRoute(metadata.route, primaryTask.id);
        }

        if (!cancelled) {
          setState({
            data: { metadata, devices, events, tasks, trajectory, dataOrigin: 'api' },
            loading: false,
          });
        }
      } catch {
        // API 不可用：本地演示数据兜底
        const metadata = buildFallbackSceneMetadata(engine);
        const tasks = buildFallbackTasks(engine);
        const primaryTask = tasks[0]!;
        if (!cancelled) {
          setState({
            data: {
              metadata,
              devices: buildFallbackDevices(engine),
              events: buildFallbackEvents(engine, primaryTask.id),
              tasks,
              trajectory: deriveTrajectoryFromRoute(metadata.route, primaryTask.id),
              dataOrigin: 'fallback',
            },
            loading: false,
          });
        }
        }
      } catch {
        // 连本地场景资产都不可用时，放弃加载（页面停留在加载提示）
        if (!cancelled) setState({ data: null, loading: false });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

/** Recorded Runs must never be filled from a demonstration scene route. */
export function mayFillTrajectoryFromSceneRoute(runKind: string | undefined): boolean {
  return runKind !== 'recorded';
}

/** 指定任务的轨迹：API 优先，失败时由场景路线派生。 */
export function useTaskTrajectory(
  taskId: string | null,
  metadata: SceneMetadata | null,
  options?: { fillFromSceneRoute?: boolean },
): { data: TrajectoryPoint[]; loading: boolean } {
  const fillFromSceneRoute = options?.fillFromSceneRoute ?? true;
  const [state, setState] = useState<{ data: TrajectoryPoint[]; loading: boolean }>({
    data: [],
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    if (!taskId || (fillFromSceneRoute && !metadata)) {
      setState({ data: [], loading: false });
      return;
    }
    setState({ data: [], loading: true });
    const fallback =
      fillFromSceneRoute && metadata
        ? deriveTrajectoryFromRoute(metadata.route, taskId)
        : [];
    api
      .getTaskReplayTrajectory(taskId)
      .catch(() => fallback)
      .then((points) => {
        if (cancelled) return;
        setState({
          data: points.length > 0 ? points : fallback,
          loading: false,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, metadata, fillFromSceneRoute]);

  return state;
}

/** Display Trajectory for drawing. Recorded Runs never fall back to a scene route. */
export function useDisplayTrajectory(
  taskId: string | null,
  metadata: SceneMetadata | null,
  options?: { fillFromSceneRoute?: boolean },
): { data: TrajectoryPoint[]; disclaimer: string | null; loading: boolean } {
  const fillFromSceneRoute = options?.fillFromSceneRoute ?? true;
  const [state, setState] = useState<{
    data: TrajectoryPoint[];
    disclaimer: string | null;
    loading: boolean;
  }>({
    data: [],
    disclaimer: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    if (!taskId || (fillFromSceneRoute && !metadata)) {
      setState({ data: [], disclaimer: null, loading: false });
      return;
    }
    setState({ data: [], disclaimer: null, loading: true });
    const fallback =
      fillFromSceneRoute && metadata
        ? deriveTrajectoryFromRoute(metadata.route, taskId)
        : [];
    api
      .getTaskDisplayTrajectory(taskId)
      .then((payload: DisplayTrajectory) => {
        if (cancelled) return;
        const points = payload.points.length > 0 ? payload.points : fallback;
        setState({
          data: points,
          disclaimer: payload.disclaimer,
          loading: false,
        });
      })
      .catch(() => {
        if (cancelled) return;
        setState({ data: fallback, disclaimer: null, loading: false });
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, metadata, fillFromSceneRoute]);

  return state;
}

/** Load Scene Metadata bound to a Recorded Run's original Scene Version. */
export function useBoundSceneMetadata(
  sceneId: string | null,
  sceneVersionId: string | null,
): { data: SceneMetadata | null; loading: boolean } {
  const [state, setState] = useState<{ data: SceneMetadata | null; loading: boolean }>({
    data: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    if (!sceneId) {
      setState({ data: null, loading: false });
      return;
    }
    setState({ data: null, loading: true });
    const params = sceneVersionId ? { scene_version_id: sceneVersionId } : {};
    api
      .getSceneMetadata(sceneId, params)
      .then((metadata) => {
        if (cancelled) return;
        if (sceneVersionId && metadata.scene_version_id !== sceneVersionId) {
          setState({ data: null, loading: false });
          return;
        }
        setState({ data: metadata, loading: false });
      })
      .catch(() => {
        if (!cancelled) setState({ data: null, loading: false });
      });
    return () => {
      cancelled = true;
    };
  }, [sceneId, sceneVersionId]);

  return state;
}
