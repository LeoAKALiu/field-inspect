import type {
  DataSourceStatus,
  DetectionEvent,
  DeviceType,
  DisplayTrajectory,
  ErrorResponse,
  EventExportRequest,
  EventSeverity,
  EventStatus,
  EventStatusUpdate,
  EventType,
  PatrolTask,
  RunKind,
  Scene,
  SceneDetail,
  SceneMetadata,
  SceneVersion,
  SensorDevice,
  SensorReading,
  TaskMode,
  TaskStatus,
  TrajectoryPoint,
} from '@digital-twin/contracts';

/** 契约 v2：REST 前缀 /api */
const API_BASE = '/api';

export class ApiRequestError extends Error {
  status: number;
  code: string;
  details?: Record<string, unknown>;

  constructor(status: number, body: ErrorResponse) {
    super(body.error.message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.code = body.error.code;
    this.details = body.error.details;
  }
}

function qs(params: Record<string, string | number | undefined>): string {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      usp.set(key, String(value));
    }
  }
  const s = usp.toString();
  return s ? `?${s}` : '';
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as ErrorResponse | null;
    if (body?.error) throw new ApiRequestError(res.status, body);
    throw new Error(`API request failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listScenes: () => request<Scene[]>('/scenes'),
  getScene: (sceneId: string) => request<SceneDetail>(`/scenes/${sceneId}`),
  getSceneMetadata: (sceneId: string, params: { scene_version_id?: string } = {}) =>
    request<SceneMetadata>(`/scenes/${sceneId}/metadata${qs(params)}`),
  getSceneVersion: (sceneVersionId: string) =>
    request<SceneVersion>(`/scene-versions/${sceneVersionId}`),

  listTasks: (
    params: { scene_id?: string; status?: TaskStatus; mode?: TaskMode; run_kind?: RunKind } = {},
  ) => request<PatrolTask[]>(`/tasks${qs(params)}`),
  getTask: (taskId: string) => request<PatrolTask>(`/tasks/${taskId}`),
  getTaskTrajectory: (taskId: string, params: { from_seq?: number; limit?: number } = {}) =>
    request<TrajectoryPoint[]>(`/tasks/${taskId}/trajectory${qs(params)}`),
  /** Page through Replay Trajectory until the authoritative series is complete. */
  getTaskReplayTrajectory: async (taskId: string): Promise<TrajectoryPoint[]> => {
    const pageSize = 10_000;
    const points: TrajectoryPoint[] = [];
    let fromSeq = 0;
    for (;;) {
      const page = await api.getTaskTrajectory(taskId, { from_seq: fromSeq, limit: pageSize });
      points.push(...page);
      if (page.length < pageSize) {
        return points;
      }
      fromSeq = page[page.length - 1]!.seq + 1;
    }
  },
  getTaskDisplayTrajectory: (taskId: string) =>
    request<DisplayTrajectory>(`/tasks/${taskId}/display-trajectory`),

  listEvents: (
    params: {
      scene_id?: string;
      task_id?: string;
      status?: EventStatus;
      severity?: EventSeverity;
      type?: EventType;
      limit?: number;
      offset?: number;
    } = {},
  ) => request<DetectionEvent[]>(`/events${qs(params)}`),
  getEvent: (eventId: string) => request<DetectionEvent>(`/events/${eventId}`),
  updateEventStatus: (eventId: string, body: EventStatusUpdate) =>
    request<DetectionEvent>(`/events/${eventId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  /** POST /api/events/export — 契约 v2 导出（返回文件 Blob）。 */
  exportEvents: async (body: EventExportRequest): Promise<Blob> => {
    const res = await fetch(`${API_BASE}/events/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`导出失败（${res.status}）`);
    return res.blob();
  },

  listDevices: (params: { scene_id?: string; type?: DeviceType } = {}) =>
    request<SensorDevice[]>(`/devices${qs(params)}`),
  getDeviceReadings: (
    deviceId: string,
    params: { from?: string; to?: string; interval_sec?: number } = {},
  ) => request<SensorReading[]>(`/devices/${deviceId}/readings${qs(params)}`),

  listDataSources: () => request<DataSourceStatus[]>('/data-sources'),
};

/** 契约 v2 WebSocket 通道（经 vite 代理 /ws → 后端）。 */
export function wsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${path}`;
}

/** WS /ws/vehicle?task_id= — 车辆位置推送。 */
export function vehicleSocketUrl(taskId: string): string {
  return wsUrl(`/ws/vehicle?task_id=${encodeURIComponent(taskId)}`);
}

/** WS /ws/replay/{task_id} — 任务回放通道。 */
export function replaySocketUrl(taskId: string): string {
  return wsUrl(`/ws/replay/${encodeURIComponent(taskId)}`);
}
