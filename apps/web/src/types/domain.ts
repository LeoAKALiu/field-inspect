/**
  Web 端展示层类型。
  领域对象一律使用 @digital-twin/contracts（契约 v2）；
  本文件只保留页面展示所需的本地形状与契约类型的别名。
 */
import type { DeviceType, EventSeverity, EventType, SourceType } from '@digital-twin/contracts';

/** 数据源模式：与契约 SourceType 完全对齐。 */
export type DataSourceMode = SourceType;

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

/** 设备表格/详情展示形状（由契约 SensorDevice 经 adapters 映射得到）。 */
export interface EquipmentItem {
  id: string;
  code: string;
  kind: DeviceType;
  label: string;
  /** 展示用位置文本（桩号或坐标）。 */
  chainage: string;
  position: Vec3;
  status: 'online' | 'offline';
  statusName: string;
  unit: string;
  lastTelemetryValue: string;
  meta?: Record<string, unknown>;
}

/** 缺陷复核展示形状（由契约 DetectionEvent 经 adapters 映射得到）。 */
export interface AnomalyReviewItem {
  id: string;
  position: Vec3;
  severity?: EventSeverity;
  label?: string;
  /** 播放时刻（秒），用于回放联动。 */
  time?: number;
  chainage: string;
  defectType: EventType;
  defectTypeName: string;
  reviewStatus: 'pending' | 'verified' | 'false_positive' | 'rectified';
  reviewStatusName: string;
  confidence: number;
  detectedAt: string;
  assignedEngineer?: string;
  notes?: string;
  imageUrl?: string;
  meta?: Record<string, unknown>;
}
