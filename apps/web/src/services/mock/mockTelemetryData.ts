/** Generates ECharts option specs and time series series for industrial monitoring UI */

export function get24HourTelemetryOption() {
  const times = Array.from({ length: 24 }, (_, i) => `${i.toString().padStart(2, '0')}:00`);
  const delaminationSeries = [0.38, 0.40, 0.41, 0.41, 0.42, 0.43, 0.42, 0.44, 0.45, 0.48, 0.52, 0.55, 0.58, 0.62, 0.65, 0.68, 0.72, 0.78, 0.81, 0.83, 0.84, 0.85, 0.85, 0.85];
  const displacementSeries = [1.2, 1.2, 1.3, 1.3, 1.4, 1.5, 1.5, 1.6, 1.8, 2.0, 2.3, 2.5, 2.7, 2.9, 3.0, 3.12, 3.12, 3.10, 3.08, 3.05, 3.02, 3.00, 2.98, 2.95];
  const safetyThreshold = Array(24).fill(3.0);

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', label: { backgroundColor: '#1e293b' } },
      backgroundColor: '#121722',
      borderColor: '#232d3f',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
    },
    legend: {
      data: ['离层位移 (mm)', '墙体变形 (mm)', '预警阈值 (3.0mm)'],
      textStyle: { color: '#94a3b8', fontSize: 11 },
      top: 0,
      right: 10,
    },
    grid: {
      top: 30,
      left: 40,
      right: 20,
      bottom: 25,
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: times,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: '#334155' } },
      splitLine: { lineStyle: { color: '#1e293b', type: 'dashed' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    series: [
      {
        name: '离层位移 (mm)',
        type: 'line',
        smooth: true,
        data: delaminationSeries,
        itemStyle: { color: '#06b6d4' },
        lineStyle: { width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(6, 182, 212, 0.25)' },
              { offset: 1, color: 'rgba(6, 182, 212, 0.0)' },
            ],
          },
        },
      },
      {
        name: '墙体变形 (mm)',
        type: 'line',
        smooth: true,
        data: displacementSeries,
        itemStyle: { color: '#f59e0b' },
        lineStyle: { width: 2 },
      },
      {
        name: '预警阈值 (3.0mm)',
        type: 'line',
        data: safetyThreshold,
        itemStyle: { color: '#ef4444' },
        lineStyle: { width: 1.5, type: 'dashed' },
        symbol: 'none',
      },
    ],
  };
}

export function getDefectDistributionOption() {
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#121722',
      borderColor: '#232d3f',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: '{b}: {c}处 ({d}%)',
    },
    legend: {
      orient: 'vertical',
      right: 10,
      top: 'center',
      textStyle: { color: '#94a3b8', fontSize: 11 },
    },
    series: [
      {
        name: '缺陷类型',
        type: 'pie',
        radius: ['45%', '72%'],
        center: ['38%', '50%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 6,
          borderColor: '#0a0d14',
          borderWidth: 2,
        },
        label: {
          show: false,
        },
        data: [
          { value: 2, name: '拱顶离层', itemStyle: { color: '#ef4444' } },
          { value: 5, name: '表面剥落', itemStyle: { color: '#f59e0b' } },
          { value: 8, name: '衬砌裂缝', itemStyle: { color: '#06b6d4' } },
          { value: 3, name: '施工缝渗水', itemStyle: { color: '#3b82f6' } },
        ],
      },
    ],
  };
}

export function getMultiChannelPlaybackOption(currentTimeSec: number) {
  // Generate 60 time points representing 60 seconds around playback window
  const labels = Array.from({ length: 60 }, (_, i) => `${i}s`);
  const speed = labels.map((_, i) => 15 + Math.sin(i * 0.1) * 2 + (i === Math.floor(currentTimeSec) ? 1 : 0));
  const vibration = labels.map((_, i) => 0.05 + Math.cos(i * 0.3) * 0.03 + (i > 25 && i < 32 ? 0.12 : 0));
  const sensorSignal = labels.map((_, i) => 0.4 + (i / 60) * 0.4 + (i > 20 && i < 30 ? 0.3 : 0));

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#121722',
      borderColor: '#232d3f',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
    },
    grid: {
      top: 25,
      left: 45,
      right: 20,
      bottom: 25,
    },
    xAxis: {
      type: 'category',
      data: labels,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
    },
    yAxis: [
      {
        type: 'value',
        name: '车速(km/h)',
        nameTextStyle: { color: '#64748b', fontSize: 10 },
        splitLine: { lineStyle: { color: '#1e293b', type: 'dashed' } },
        axisLabel: { color: '#64748b', fontSize: 10 },
      },
      {
        type: 'value',
        name: '离层(mm)',
        nameTextStyle: { color: '#64748b', fontSize: 10 },
        splitLine: { show: false },
        axisLabel: { color: '#64748b', fontSize: 10 },
      },
    ],
    series: [
      {
        name: '巡检车速 (km/h)',
        type: 'line',
        smooth: true,
        data: speed,
        itemStyle: { color: '#3b82f6' },
        lineStyle: { width: 1.5 },
      },
      {
        name: '车体震动G值',
        type: 'line',
        smooth: true,
        data: vibration,
        itemStyle: { color: '#10b981' },
        lineStyle: { width: 1.5 },
      },
      {
        name: '离层响应(mm)',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        data: sensorSignal,
        itemStyle: { color: '#06b6d4' },
        lineStyle: { width: 2 },
      },
    ],
  };
}

export function getDeviceHistoryOption(deviceCode: string) {
  const days = ['08-01', '08-02', '08-03', '08-04', '08-05', '08-06', '08-07'];
  const values = [0.35, 0.38, 0.40, 0.42, 0.85, 0.84, 0.85];

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#121722',
      borderColor: '#232d3f',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
    },
    grid: {
      top: 20,
      left: 35,
      right: 15,
      bottom: 25,
    },
    xAxis: {
      type: 'category',
      data: days,
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
        name: `${deviceCode} 历史测量值`,
        type: 'bar',
        barWidth: '40%',
        data: values,
        itemStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: '#06b6d4' },
              { offset: 1, color: '#3b82f6' },
            ],
          },
          borderRadius: [4, 4, 0, 0],
        },
      },
    ],
  };
}
