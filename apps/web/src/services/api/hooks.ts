import { useEffect, useState } from 'react';
import type { DataSourceStatus, DetectionEvent, SceneMetadata, SensorDevice, SensorReading } from '@digital-twin/contracts';
import { api } from './client';

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

function useAsync<T>(fetcher: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ data: null, loading: true, error: null });

  useEffect(() => {
    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true, error: null }));
    fetcher()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ data: null, loading: false, error: err instanceof Error ? err.message : String(err) });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}

/** GET /api/data-sources — simulation/replay/live_pending availability. */
export function useDataSources(): AsyncState<DataSourceStatus[]> {
  return useAsync(() => api.listDataSources(), []);
}

/** GET /api/scenes/{id}/metadata — 场景元数据（资产 URL、巡检路线、坐标系声明）。 */
export function useSceneMetadata(sceneId: string | null): AsyncState<SceneMetadata> {
  return useAsync(
    () => (sceneId ? api.getSceneMetadata(sceneId) : Promise.reject(new Error('no scene'))),
    [sceneId],
  );
}

/** GET /api/events — real detection events for review workflows. */
export function useEvents(params: { scene_id?: string; task_id?: string } = {}): AsyncState<DetectionEvent[]> {
  return useAsync(() => api.listEvents(params), [params.scene_id, params.task_id]);
}

/** GET /api/devices — real sensor devices registered on the backend. */
export function useDevices(params: { scene_id?: string } = {}): AsyncState<SensorDevice[]> {
  return useAsync(() => api.listDevices(params), [params.scene_id]);
}

/** GET /api/devices/{id}/readings — deterministic simulated reading curve for one device. */
export function useDeviceReadings(
  deviceId: string | null,
  params: { from?: string; to?: string; interval_sec?: number } = {},
): AsyncState<SensorReading[]> {
  return useAsync(
    () => (deviceId ? api.getDeviceReadings(deviceId, params) : Promise.resolve([])),
    [deviceId, params.from, params.to, params.interval_sec],
  );
}
