import type { DeviceType, EventSeverity, SourceType } from '@digital-twin/contracts';

/** Recorded vs Demonstration public labels. Operator Label is not used here. */
export function recordedRunLabel(task: {
  run_kind: string;
  acceptance_state: string;
}): string {
  if (task.run_kind !== 'recorded') return '演示运行';
  if (task.acceptance_state === 'accepted') return '现场已验收';
  if (task.acceptance_state === 'withdrawn') return '现场实录，已撤销';
  return '现场实录，待验收';
}

/** 缺陷严重级别中文标签 */
export const SEVERITY_LABELS: Record<EventSeverity, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  critical: '严重',
};

export function severityLabel(severity?: EventSeverity): string {
  return severity ? SEVERITY_LABELS[severity] : '未评级';
}

/** 设备类型中文标签（契约 DeviceType） */
export const DEVICE_TYPE_LABELS: Record<DeviceType, string> = {
  delamination: '离层仪',
  displacement: '位移计',
  convergence: '收敛计',
  stress: '应力计',
  temperature: '温度传感器',
  humidity: '湿度传感器',
  gas: '气体传感器',
};

/** 数据来源中文标签（契约 SourceType） */
export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  simulation: '模拟数据',
  replay: '历史回放',
  live_pending: '实时接入',
};
