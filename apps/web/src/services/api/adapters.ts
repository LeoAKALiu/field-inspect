import type { DetectionEvent, EventStatus, EventType, SensorDevice, SensorReading } from '@digital-twin/contracts';
import type { AnomalyReviewItem, EquipmentItem } from '../../types/domain';
import { DEVICE_TYPE_LABELS } from '../../utils/labels';

export const EVENT_TYPE_LABELS: Record<EventType, string> = {
  crack: '衬砌裂缝',
  water_leakage: '渗漏水',
  spalling: '混凝土剥落',
  corrosion: '钢筋锈蚀',
  equipment_fault: '设备故障',
  obstacle: '异物侵限',
};

export type ReviewStatus = AnomalyReviewItem['reviewStatus'];

const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  pending: '待专家复核',
  verified: '已复核确认',
  false_positive: '已判定误报',
  rectified: '已归档完成',
};

/** Backend event.status -> UI review workflow status (both model open -> ack -> resolved|false_positive). */
export function mapEventStatusToReviewStatus(status: EventStatus): ReviewStatus {
  switch (status) {
    case 'open':
      return 'pending';
    case 'acknowledged':
      return 'verified';
    case 'resolved':
      return 'rectified';
    case 'false_positive':
      return 'false_positive';
    default: {
      const _exhaustive: never = status;
      return _exhaustive;
    }
  }
}

/**
 * Real backend DetectionEvent -> AnomalyReviewItem (the display shape the review page
 * was originally built around). `meta.rawStatus` carries the untranslated backend
 * status so the page can enforce the real open -> acknowledged -> resolved|false_positive
 * transition rules instead of guessing from the localized label.
 */
export function eventToAnomalyReviewItem(e: DetectionEvent): AnomalyReviewItem {
  return {
    id: e.id,
    position: e.position,
    severity: e.severity,
    label: e.description || EVENT_TYPE_LABELS[e.type] || e.type,
    time: undefined,
    chainage: `X:${e.position.x.toFixed(1)} / Y:${e.position.y.toFixed(1)} / Z:${e.position.z.toFixed(1)}`,
    defectType: e.type,
    defectTypeName: EVENT_TYPE_LABELS[e.type] ?? e.type,
    reviewStatus: mapEventStatusToReviewStatus(e.status),
    reviewStatusName: REVIEW_STATUS_LABELS[mapEventStatusToReviewStatus(e.status)],
    confidence: e.confidence ?? 0,
    detectedAt: e.detected_at,
    assignedEngineer: e.handled_by ?? undefined,
    notes: e.handle_comment ?? undefined,
    meta: { rawStatus: e.status },
  };
}

/**
 * 契约 SensorDevice -> 设备表格/详情展示形状。
 * 契约字段之外的展示列（读数等）在没有接入真实遥测时以占位显示。
 */
export function deviceToEquipmentItem(d: SensorDevice): EquipmentItem {
  return {
    id: d.id,
    code: d.id,
    kind: d.type,
    label: d.name || DEVICE_TYPE_LABELS[d.type] || d.type,
    chainage: d.position
      ? `X:${d.position.x.toFixed(1)} / Y:${d.position.y.toFixed(1)} / Z:${d.position.z.toFixed(1)}`
      : '未绑定点位',
    position: d.position ?? { x: 0, y: 0, z: 0 },
    status: d.status,
    statusName: d.status === 'online' ? '在线' : '离线',
    unit: d.unit,
    lastTelemetryValue: '—',
    meta: { provenance: d.provenance },
  };
}

/**
 * ECharts line option for GET /devices/{id}/readings — deterministic
 * (sine + fixed-seed noise) simulated sensor curve served by the backend.
 */
export function readingsToLineOption(readings: SensorReading[], seriesName: string) {
  const times = readings.map((r) => r.timestamp.slice(11, 16));
  const values = readings.map((r) => r.value);
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#121722',
      borderColor: '#232d3f',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
    },
    grid: { top: 20, left: 40, right: 15, bottom: 25 },
    xAxis: {
      type: 'category',
      data: times,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#1e293b', type: 'dashed' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    series: [
      {
        name: seriesName,
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: values,
        itemStyle: { color: '#06b6d4' },
        lineStyle: { width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(6, 182, 212, 0.25)' },
              { offset: 1, color: 'rgba(6, 182, 212, 0.0)' },
            ],
          },
        },
      },
    ],
  };
}
