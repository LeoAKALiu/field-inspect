/**
 * 领域契约 v2（冻结）+ v0.3 领域扩展（增量）— 地下工程巡检车数字孪生原型
 *
 * 与 packages/contracts/domain.schema.json、openapi.yaml 保持同步。
 * 任何变更须经全栈负责人评审并同步全部契约文件。
 *
 * 版本约定：
 * - v2 对象（Scene / PatrolTask / VehicleState / TrajectoryPoint / DetectionEvent /
 *   SensorDevice / SensorReading / SpatialBinding / DataSourceStatus / SceneMetadata）
 *   保持冻结，仅作向后兼容消费。
 * - v0.3 扩展（第 11–14 节）为纯增量：仪器观测、仪器定位、资产匹配、命名指标读数、
 *   巡检站与尝试结果、运行结果溯源与逐对象数据血缘。每个 v0.3 对象载荷携带
 *   contract_version: '0.3'；其他 contract_version 一律拒绝。
 *
 * 坐标系统一约定：
 * - 场景局部坐标系 `scene_local_yup`：右手系，Y 轴向上，单位米。
 * - 业务层（API / Web / 契约）一律使用 scene_local_yup。
 * - twin-viewer 引擎公共 API 实测同为 Y-up；为防御引擎变更，适配器将坐标转换
 *   集中在单点（当前恒等映射，一行开关可启用 (x, y, z)_yup → (x, -z, y)_zup）。
 */

// ---------------------------------------------------------------------------
// 通用
// ---------------------------------------------------------------------------

/** 三种数据来源。不存在"甲方真实数据已接入"状态。 */
export type SourceType = 'simulation' | 'replay' | 'live_pending';

export type ProvenanceStatus = 'simulated' | 'pending_confirmation' | 'confirmed';

/** 数据出处。当前不存在 status === 'confirmed' 的对象。 */
export interface Provenance {
  source: SourceType;
  status: ProvenanceStatus;
}

/** 场景局部坐标（scene_local_yup，右手系，Y 轴向上，单位米）。 */
export interface Position3D {
  x: number;
  y: number;
  z: number;
}

// ---------------------------------------------------------------------------
// 1. Scene
// ---------------------------------------------------------------------------

export type SceneStatus = 'active' | 'archived';

export interface Scene {
  id: string;
  name: string;
  description?: string;
  status: SceneStatus;
  geometry_ref?: string | null;
  bounds_min?: Position3D;
  bounds_max?: Position3D;
  length_m?: number;
  created_at: string;
  source_type: SourceType;
  provenance: Provenance;
}

export interface SceneDetail extends Scene {
  spatial_bindings: SpatialBinding[];
}

/** Immutable Scene Version identity used to bind a historical Imported Run. */
export interface SceneVersion {
  scene_version_id: string;
  scene_id: string;
  asset_sha256: string;
  navigation_default: boolean;
  alignment_id: string | null;
  evidence_sha256: string | null;
}

// ---------------------------------------------------------------------------
// 2. PatrolTask
// ---------------------------------------------------------------------------

export type TaskMode = SourceType;
export type TaskStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'completed_with_exceptions'
  | 'aborted';
export type RunKind = 'demonstration' | 'recorded';
export type AcceptanceState =
  | 'not_applicable'
  | 'pending_acceptance'
  | 'accepted'
  | 'withdrawn';
export type PackageRunStatus = 'completed' | 'completed_with_exceptions';

export interface PatrolTask {
  id: string;
  scene_id: string;
  name: string;
  mode: TaskMode;
  status: TaskStatus;
  planned_start?: string | null;
  actual_start?: string | null;
  actual_end?: string | null;
  distance_m?: number | null;
  event_count?: number;
  /** Demonstration Run vs Recorded Run. Seeded/synthetic tasks are demonstration. */
  run_kind: RunKind;
  /** Formal package status; null for demonstration tasks that were not imported. */
  package_status: PackageRunStatus | null;
  /** Field-Replay Acceptance state. Import never writes accepted/confirmed. */
  acceptance_state: AcceptanceState;
  scene_version_id: string | null;
  alignment_id: string | null;
  has_pointcloud: boolean;
  acceptance_recorded_at: string | null;
  /** Safe Field-Replay Acceptance summary; no evidence images or archive paths. */
  acceptance_summary: AcceptanceSummary | null;
  source_type: SourceType;
  provenance: Provenance;
}

export interface AcceptanceSummary {
  first_frame: 'pass' | 'fail' | null;
  last_frame: 'pass' | 'fail' | null;
  required_passed: number;
  required_total: number;
  auxiliary_failed: number;
}

// ---------------------------------------------------------------------------
// 3. VehicleState
// ---------------------------------------------------------------------------

export interface VehicleState {
  task_id: string;
  timestamp: string;
  position: Position3D;
  /** 航向角（度），[0, 360)，绕 +Y 轴。 */
  heading_deg: number;
  speed_mps: number;
  battery_pct?: number | null;
  source_type: SourceType;
  provenance: Provenance;
}

// ---------------------------------------------------------------------------
// 4. TrajectoryPoint
// ---------------------------------------------------------------------------

/** 后端轨迹采样点（REST/WS 载荷），与 twin-viewer 的播放时刻采样不同。 */
export interface TrajectoryPoint {
  task_id: string;
  /** 任务内单调递增序号。 */
  seq: number;
  timestamp: string;
  position: Position3D;
  heading_deg?: number | null;
  speed_mps?: number | null;
  source_type: SourceType;
  provenance: Provenance;
}

/** Deterministic reduction of a Replay Trajectory for drawing only. */
export interface DisplayTrajectory {
  algorithm: string;
  algorithm_version: string;
  source_point_count: number;
  display_point_count: number;
  kept_seq: number[];
  landmark_seqs: Record<string, number>;
  disclaimer: string;
  points: TrajectoryPoint[];
}

// ---------------------------------------------------------------------------
// 5. DetectionEvent
// ---------------------------------------------------------------------------

export type EventType =
  | 'crack'
  | 'water_leakage'
  | 'spalling'
  | 'corrosion'
  | 'equipment_fault'
  | 'obstacle';
export type EventSeverity = 'low' | 'medium' | 'high' | 'critical';

/**
 * 复核状态机：open → acknowledged → resolved | false_positive。
 * UI 语义映射：open=待复核，acknowledged=已确认，resolved=已闭环，false_positive=已排除。
 * 注意：异常候选不等于已确认灾害。
 */
export type EventStatus = 'open' | 'acknowledged' | 'resolved' | 'false_positive';

export interface DetectionEvent {
  id: string;
  scene_id: string;
  task_id: string;
  type: EventType;
  severity: EventSeverity;
  status: EventStatus;
  position: Position3D;
  description?: string;
  confidence?: number | null;
  /** 现场图片引用（原型为占位图）。 */
  image_ref?: string | null;
  detected_at: string;
  handled_by?: string | null;
  handled_at?: string | null;
  handle_comment?: string | null;
  source_type: SourceType;
  provenance: Provenance;
}

/** PATCH /api/events/{id} 请求体。 */
export interface EventStatusUpdate {
  status: Extract<EventStatus, 'acknowledged' | 'resolved' | 'false_positive'>;
  comment?: string;
  handled_by?: string;
}

// ---------------------------------------------------------------------------
// 6. SensorDevice
// ---------------------------------------------------------------------------

/** 离层仪 delamination / 位移计 displacement / 收敛计 convergence / 应力计 stress 等。 */
export type DeviceType =
  | 'delamination'
  | 'displacement'
  | 'convergence'
  | 'stress'
  | 'temperature'
  | 'humidity'
  | 'gas';

export type DeviceStatus = 'online' | 'offline';

export interface SensorDevice {
  id: string;
  scene_id: string;
  name: string;
  type: DeviceType;
  unit: string;
  position?: Position3D;
  status: DeviceStatus;
  source_type: SourceType;
  provenance: Provenance;
}

// ---------------------------------------------------------------------------
// 7. SensorReading
// ---------------------------------------------------------------------------

export type ReadingQuality = 'good' | 'uncertain' | 'bad';

export interface SensorReading {
  device_id: string;
  timestamp: string;
  value: number;
  quality?: ReadingQuality;
  source_type: SourceType;
  provenance: Provenance;
}

// ---------------------------------------------------------------------------
// 8. SpatialBinding
// ---------------------------------------------------------------------------

export type SpatialBindingTargetType = 'sensor_device' | 'detection_event' | 'vehicle';

export interface SpatialBinding {
  id: string;
  scene_id: string;
  target_type: SpatialBindingTargetType;
  target_id: string;
  position: Position3D;
  /** 坐标系标识，固定 scene_local_yup；与甲方 GIS/BIM 对齐待确认。 */
  coordinate_system?: string;
  source_type: SourceType;
  provenance: Provenance;
}

// ---------------------------------------------------------------------------
// 9. DataSourceStatus
// ---------------------------------------------------------------------------

export type DataSourceStatusValue = 'active' | 'standby' | 'reserved' | 'unavailable';

export interface DataSourceStatus {
  mode: SourceType;
  name: string;
  /** live_pending 源固定为 reserved（预留，未接入）。 */
  status: DataSourceStatusValue;
  last_update?: string | null;
  message?: string | null;
  source_type: SourceType;
  provenance: Provenance;
}

// ---------------------------------------------------------------------------
// 10. SceneMetadata
// ---------------------------------------------------------------------------

/**
 * 巡检路线的独立血缘。场景几何、路线与车辆业务数据可能来自不同来源，
 * 因此不能只用 SceneMetadata.source_type 概括整条融合链路。
 */
export interface RouteProvenance {
  /** 目前实现：从 PLY 自动识别主廊道候选，再作稳健中心估计与逐段连续性检查。 */
  method: 'pointcloud_auto_corridor_centerline';
  algorithm_version: string;
  source_asset: string;
  source_sha256: string;
  source_point_count: number;
  generated_point_count: number;
  /** estimated 明确表示算法估计，不是测量/验线成果。 */
  status: 'estimated' | 'measured';
  disclaimer: string;
}

/**
 * 场景元数据：三维资产引用、坐标系声明与巡检路线。
 * 前端车辆轨迹/路线不得写死在组件中，必须从此对象与 API 动态加载。
 */
export interface SceneMetadata {
  scene_id: string;
  name: string;
  /** 固定 'scene_local_yup'：右手系，Y 轴向上。 */
  coordinate_system: 'scene_local_yup';
  /** 固定 'm'。 */
  units: 'm';
  up_axis: 'Y';
  bounds_min: Position3D;
  bounds_max: Position3D;
  length_m: number;
  /** 三维网格模型 URL（相对站点根路径，离线可用）。 */
  mesh_url?: string | null;
  /** 点云 URL（相对站点根路径，离线可用）。 */
  pointcloud_url?: string | null;
  /** 巡检路线有序路径点（车辆轨迹由此派生）。 */
  route: Position3D[];
  /** 可选的路线级数据血缘；用于区分点云估计路线与实测路线。 */
  route_provenance?: RouteProvenance;
  description?: string;
  /** Present when metadata is bound to a registered Scene Version. */
  scene_version_id?: string | null;
  asset_sha256?: string | null;
  alignment_id?: string | null;
  source_type: SourceType;
  provenance: Provenance;
}

// ---------------------------------------------------------------------------
// 11. v0.3 逐对象数据血缘（per-object data lineage）
// ---------------------------------------------------------------------------

/**
 * v0.3 数据来源类别。逐对象血缘必须能区分：
 * - synthetic_fixture：显式标记的开发/迁移夹具，永不宣称客户或现场数据；
 * - simulation：模拟/演示数据；
 * - replay：已导入运行的历史回放数据；
 * - live_pending：甲方实时接口预留（当前恒不接入）。
 * 该类别与 v2 SourceType 并存：v2 对象继续使用 source_type/provenance；
 * v0.3 对象同时携带 source_type、provenance 与本血缘对象。
 */
export type DataSourceKind = 'synthetic_fixture' | 'simulation' | 'replay' | 'live_pending';

/** 逐对象数据血缘：说明该对象由谁、以什么版本、从什么上游派生。 */
export interface DataLineage {
  source: DataSourceKind;
  /** 生成/采集该对象的检测器、算法或导出工具名；null 表示未登记。 */
  producer: string | null;
  /** producer 的版本；null 表示未登记。 */
  producer_version: string | null;
  /**
   * 上游来源引用（如黄金夹具文件名、导入的 run/package 引用）。
   * 只允许引用标识，不得携带客户数据内容、连续原始视频或原始 MCAP。
   */
  derived_from: string | null;
}

// ---------------------------------------------------------------------------
// 12. v0.3 仪器观测（instrument observation）
// ---------------------------------------------------------------------------

/**
 * 检测器种类。v0.3 仅包含 marker_proxy：以预登记基准标记（AprilTag 等）为代理
 * 的仪器观测检测器。marker_proxy 明确不等于通用图像识别，其输出不构成
 * 已确认的客户设备 ID，也不上传/展示连续原始视频。
 */
export type InstrumentDetectorKind = 'marker_proxy';

/** 采集帧内的归一化图像区域（比例坐标，与分辨率解耦）。 */
export interface ImageRegion {
  /** 归一化 [0,1]，左上角为原点。 */
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
  /** 采集帧像素尺寸（≥1），用于审计与复现。 */
  image_width: number;
  image_height: number;
}

/** 观测使用的检测器及其版本。 */
export interface InstrumentDetector {
  kind: InstrumentDetectorKind;
  /** 检测器实现版本（如 'apriltag_36h11_v1'）。 */
  version: string;
}

/**
 * 仪器观测：车辆侧相机对注册仪器/基准标记的一次可追溯检测记录。
 * 稳定 ID 在运行内唯一且不可复用；观测本身不解释业务含义。
 */
export interface InstrumentObservation {
  contract_version: '0.3';
  /** 稳定观测 ID（运行内唯一，永不复用）。 */
  observation_id: string;
  /** 所属巡检运行 ID（对应 PatrolTask.id / 包 run_id）。 */
  run_id: string;
  /** 采集时间（ISO 8601 UTC，采集时刻而非到达时刻）。 */
  captured_at: string;
  /** 采集帧内归一化图像区域。 */
  image_region: ImageRegion;
  /** 检测器种类/版本（v0.3 仅 marker_proxy）。 */
  detector: InstrumentDetector;
  /** 检测置信度 [0,1]。 */
  confidence: number;
  /** 标定引用：本次观测所依赖的相机标定/对齐证据标识（如 SHA-256 或版本 ID）。 */
  calibration_ref: string;
  source_type: SourceType;
  provenance: Provenance;
  lineage: DataLineage;
}

// ---------------------------------------------------------------------------
// 13. v0.3 仪器定位与资产匹配（相互独立的两个对象）
// ---------------------------------------------------------------------------

/** 定位/匹配共用的三态结果。 */
export type ResolutionStatus = 'matched' | 'unmatched' | 'needs_review';

/**
 * 仪器定位：把一次仪器观测解析到 scene_local_yup 场景位姿。
 * 与资产匹配相互独立：定位成功不代表资产身份已确认。
 */
export interface InstrumentLocalization {
  contract_version: '0.3';
  localization_id: string;
  run_id: string;
  /** 被解析的仪器观测 ID。 */
  observation_id: string;
  status: ResolutionStatus;
  /** scene_local_yup 位置；unmatched 时必须为 null。 */
  position: Position3D | null;
  /** 航向角（度），[0,360)；unmatched 时必须为 null。 */
  heading_deg: number | null;
  /** 解析方法标识（如 'marker_pose_scene_transform'）。 */
  method: string;
  /** 定位残差（米）；无法估计时为 null。 */
  residual_m: number | null;
  source_type: SourceType;
  provenance: Provenance;
  lineage: DataLineage;
}

/**
 * 资产匹配：把一次仪器定位关联到场景登记的仪器资产 ID。
 * matched 仅在登记资产唯一明确时成立；unmatched / needs_review 不产生
 * 任何已确认的客户设备 ID。与仪器定位是两个独立对象，可各自复核。
 */
export interface AssetMatch {
  contract_version: '0.3';
  match_id: string;
  run_id: string;
  /** 被匹配的仪器定位 ID。 */
  localization_id: string;
  status: ResolutionStatus;
  /** 已确认的登记资产 ID；仅 status === 'matched' 时非 null。 */
  asset_id: string | null;
  /**
   * needs_review 时的候选资产 ID（≥1）；matched 必须为空数组，
   * unmatched 必须为空数组。
   */
  candidate_asset_ids: string[];
  /** 匹配规则版本（可审计，如 'registry_unique_v1'）。 */
  rule_version: string;
  source_type: SourceType;
  provenance: Provenance;
  lineage: DataLineage;
}

// ---------------------------------------------------------------------------
// 14. v0.3 命名指标读数 / 巡检站 / 运行结果溯源
// ---------------------------------------------------------------------------

/**
 * 命名指标。离层仪读数由深基点、浅基点与差值三个命名 metric 表达，
 * 不再复用单一匿名 value 语义。
 */
export type InstrumentMetricName = 'deep_base' | 'shallow_base' | 'delta';

export interface NamedMetric {
  name: InstrumentMetricName;
  /** 指标值（有限数）。 */
  value: number;
  /** 计量单位（离层仪典型为 'mm'）。 */
  unit: string;
}

/**
 * 仪器读数：某个已登记仪器资产在一个运行内的一组命名指标。
 * valid_until 是审计有效期；过期读数不得作为当前状态展示，
 * 缺失读数必须保持显式缺失，不得用演示数据补齐。
 */
export interface InstrumentReading {
  contract_version: '0.3';
  reading_id: string;
  run_id: string;
  /** 已登记仪器资产 ID（与 AssetMatch.asset_id 同一登记空间）。 */
  asset_id: string;
  captured_at: string;
  /** 命名指标集合（至少 1 项；同一 name 不得重复）。 */
  metrics: NamedMetric[];
  quality: ReadingQuality;
  /** 审计有效期（ISO 8601 UTC）；null 表示不适用。 */
  valid_until: string | null;
  source_type: SourceType;
  provenance: Provenance;
  lineage: DataLineage;
}

/**
 * 巡检站：具有独立稳定 ID 的物理检查位置，带目标位姿与注册仪器。
 * 巡检站不等同于基准 Tag/AprilTag；标记只是车辆侧证据或对齐辅助。
 */
export interface InspectionStation {
  contract_version: '0.3';
  station_id: string;
  scene_id: string;
  name: string;
  /** scene_local_yup 目标停车位姿。 */
  target_pose: Position3D;
  /** 目标航向角（度），[0,360)。 */
  target_heading_deg: number;
  /** 注册仪器资产 ID 列表（允许为空；空表示本站无注册仪器）。 */
  registered_instruments: string[];
  description?: string | null;
  source_type: SourceType;
  provenance: Provenance;
  lineage: DataLineage;
}

/** 站点尝试结果与明确原因。 */
export type StationAttemptResult = 'success' | 'failed' | 'skipped';

/**
 * 尝试失败/跳过的原因码。failed/skipped 必须携带其中之一；
 * success 的 reason_code 必须为 null。
 */
export type StationAttemptReasonCode =
  | 'observation_failed'
  | 'localization_unresolved'
  | 'asset_unmatched'
  | 'instrument_unavailable'
  | 'operator_skipped'
  | 'route_interrupted';

/**
 * 一次站点尝试。同一运行内对同一站点的重试表现为多条记录，
 * attempt_seq 单调递增（>1 即发生过 retry），可追溯。
 */
export interface StationAttempt {
  contract_version: '0.3';
  attempt_id: string;
  run_id: string;
  station_id: string;
  /** 运行内对该站点的尝试序号，从 1 开始单调递增。 */
  attempt_seq: number;
  started_at: string;
  ended_at: string;
  result: StationAttemptResult;
  /** failed/skipped 必填；success 必须为 null。 */
  reason_code: StationAttemptReasonCode | null;
  /** 人工可读补充说明；不得包含客户数据内容。 */
  reason_detail: string | null;
  source_type: SourceType;
  provenance: Provenance;
  lineage: DataLineage;
}

/** 运行终态。completed_with_exceptions 语义与包契约一致。 */
export type InspectionRunFinalStatus = 'completed' | 'completed_with_exceptions' | 'aborted';

export type RunAbortReasonCode = 'operator_abort' | 'safety_stop' | 'system_fault';

export interface RunAbort {
  reason_code: RunAbortReasonCode;
  /** 人工可读补充说明；不得包含客户数据内容。 */
  detail: string | null;
}

/**
 * 巡检运行结果溯源：以运行级终态 + 站点级 skip/retry 汇总表达可追溯结果。
 * - completed：无 skip、无 retry、无 abort；
 * - completed_with_exceptions：至少一个 skip 或 retry（与包契约的带例外完成一致）；
 * - aborted：必须携带 abort 原因。
 * 站点级明细见 StationAttempt 记录。
 */
export interface InspectionRunOutcome {
  contract_version: '0.3';
  run_id: string;
  final_status: InspectionRunFinalStatus;
  /** 被显式跳过的站点 ID。 */
  skipped_station_ids: string[];
  /** 发生过重试（attempt_seq > 1）的站点 ID。 */
  retried_station_ids: string[];
  /** abort 原因；仅 final_status === 'aborted' 时非 null。 */
  abort: RunAbort | null;
  source_type: SourceType;
  provenance: Provenance;
  lineage: DataLineage;
}



// ---------------------------------------------------------------------------
// 实时接入预留（不虚构真实接入）
// ---------------------------------------------------------------------------

/** POST /api/live/ingest 请求体。预留：服务端恒返回 501，待甲方协议确认后启用。 */
export interface LiveIngestEnvelope {
  source_id: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// 事件导出
// ---------------------------------------------------------------------------

/** POST /api/events/export 请求体。 */
export interface EventExportRequest {
  format: 'csv' | 'json';
  scene_id?: string;
  task_id?: string;
  status?: EventStatus;
  severity?: EventSeverity;
  type?: EventType;
}

// ---------------------------------------------------------------------------
// 错误
// ---------------------------------------------------------------------------

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ErrorResponse {
  error: ApiError;
}

// ---------------------------------------------------------------------------
// WebSocket 契约（镜像 websocket-events.schema.json）
// 通道：WS /ws/vehicle?task_id=（车辆位置推送）；WS /ws/replay/{task_id}（任务回放）。
// ---------------------------------------------------------------------------

export interface WsEnvelope<TPayload = unknown> {
  type: string;
  ts: string;
  payload: TPayload;
}

export interface ConnectionAckPayload {
  server_time: string;
  channel: 'vehicle' | 'replay';
  task_id?: string | null;
}

export interface ReplayStartedPayload {
  task_id: string;
  total_points: number;
  speed: number;
}

export interface ReplayPointPayload {
  point: TrajectoryPoint;
  progress: number;
}

export interface ReplayFinishedPayload {
  task_id: string;
  total_points: number;
}

export type WsServerMessage =
  | (WsEnvelope<ConnectionAckPayload> & { type: 'connection.ack' })
  | (WsEnvelope<VehicleState> & { type: 'vehicle.state' })
  | (WsEnvelope<ReplayStartedPayload> & { type: 'replay.started' })
  | (WsEnvelope<ReplayPointPayload> & { type: 'replay.point' })
  | (WsEnvelope<ReplayFinishedPayload> & { type: 'replay.finished' })
  | (WsEnvelope<DetectionEvent> & { type: 'event.created' })
  | (WsEnvelope<DataSourceStatus> & { type: 'datasource.status' })
  | (WsEnvelope<ApiError> & { type: 'error' });

export type ReplayControlAction = 'pause' | 'resume' | 'seek' | 'speed';

export interface ReplayControlPayload {
  action: ReplayControlAction;
  seq?: number;
  speed?: number;
}

/** 客户端 → 服务端消息（仅回放通道）。 */
export interface ReplayControlMessage {
  type: 'replay.control';
  payload: ReplayControlPayload;
}
