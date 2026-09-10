import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { PatrolTask } from '@digital-twin/contracts';
import {
  get24HourTelemetryOption,
  getDefectDistributionOption,
} from '../services/mock/mockTelemetryData';
import { useSceneData } from '../services/scene/sceneData';
import { useDataSource } from '../services/dataSource';
import {
  sampleTrajectoryAt,
  toTwinTrajectory,
} from '../services/mock/sceneFallback';
import { TwinViewerAdapter } from '../components/TwinViewer/TwinViewerAdapter';
import { DataLineage } from '../components/Common/DataLineage';
import { DEVICE_TYPE_LABELS, SOURCE_TYPE_LABELS, severityLabel } from '../utils/labels';
import {
  ShieldCheck,
  Activity,
  AlertCircle,
  Truck,
  TrendingUp,
  MapPin,
  Clock,
  Database,
} from 'lucide-react';

const TASK_STATUS_LABELS: Record<PatrolTask['status'], string> = {
  pending: '待启动',
  running: '进行中',
  completed: '已完成',
  completed_with_exceptions: '完成但有例外',
  aborted: '已中止',
};

export const OverallSituationPage: React.FC = () => {
  const { data: scene, loading } = useSceneData();
  const { mode } = useDataSource();

  const twinTrajectory = useMemo(
    () => (scene ? toTwinTrajectory(scene.trajectory) : []),
    [scene],
  );

  const vehicleSample = scene ? sampleTrajectoryAt(scene.trajectory, 30) : null;
  const vehiclePose = vehicleSample
    ? { position: vehicleSample.position, heading_deg: vehicleSample.heading_deg ?? 0 }
    : null;

  if (loading || !scene) {
    return (
      <div className="page">
        <div className="page-header">
          <h2 className="page-title">综合态势</h2>
          <p className="page-desc">隧道结构安全、监测设备与巡检任务的整体运行概览</p>
        </div>
        <div className="no-select-tip"><p>正在加载场景数据…</p></div>
      </div>
    );
  }

  const currentTask = scene.tasks.find((t) => t.status === 'running') ?? scene.tasks[0] ?? null;

  const deviceTypeCounts = scene.devices.reduce<Record<string, number>>((acc, d) => {
    acc[d.type] = (acc[d.type] ?? 0) + 1;
    return acc;
  }, {});

  const speedKmh = vehicleSample?.speed_mps != null ? vehicleSample.speed_mps * 3.6 : null;

  return (
    <div className="page">
      <div className="page-header">
        <h2 className="page-title">综合态势</h2>
        <p className="page-desc">隧道结构安全、监测设备与巡检任务的整体运行概览</p>
      </div>

      {/* KPI 概览条 */}
      <div className="kpi-strip">
        <div className="stat-card">
          <span className="stat-name">当前任务</span>
          <div className="stat-val">{currentTask ? currentTask.name : '暂无任务'}</div>
          {currentTask && (
            <span className="stat-sub">{TASK_STATUS_LABELS[currentTask.status]}</span>
          )}
        </div>
        <div className="stat-card">
          <span className="stat-name">车辆状态</span>
          <div className="stat-val text-green">
            {speedKmh != null ? `巡检中 · ${speedKmh.toFixed(1)} km/h` : '待命'}
          </div>
          <span className="stat-sub">轨迹为模拟数据</span>
        </div>
        <div className="stat-card">
          <span className="stat-name">监测点位</span>
          <div>
            <span className="kpi-number">{scene.devices.length}</span>
            <span className="kpi-unit">处</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-name">异常候选</span>
          <div>
            <span className="kpi-number" style={{ color: 'var(--amber)' }}>{scene.events.length}</span>
            <span className="kpi-unit">条</span>
          </div>
        </div>
        <div className="stat-card">
          <span className="stat-name">数据来源</span>
          <div className="stat-val text-cyan">
            <Database size={14} style={{ marginRight: 6, verticalAlign: '-2px' }} />
            LIRIS 几何 + {SOURCE_TYPE_LABELS[mode]}
          </div>
          <span className="stat-sub">
            {scene.dataOrigin === 'api' ? 'API 契约 · 混合数据血缘' : '本地兜底 · 混合数据血缘'}
          </span>
        </div>
      </div>

      <DataLineage routeProvenance={scene.metadata.route_provenance} />

      <div className="overall-grid">
        {/* LEFT COLUMN */}
        <div className="grid-column left-column">
          {/* Tunnel Overview Card */}
          <div className="panel-card">
            <div className="panel-header">
              <div className="header-title">
                <ShieldCheck className="text-cyan" size={18} />
                <span>场景概况</span>
              </div>
              <span className="status-badge normal">结构安全</span>
            </div>
            <div className="panel-body">
              <div className="tunnel-kpi-row">
                <div className="kpi-block">
                  <span className="kpi-label">场景名称</span>
                  <span className="kpi-text">{scene.metadata.name}</span>
                </div>
                <div className="kpi-block">
                  <span className="kpi-label">巡检路线全长</span>
                  <div>
                    <span className="kpi-number">{scene.metadata.length_m}</span>
                    <span className="kpi-unit">m</span>
                  </div>
                </div>
                <div className="kpi-block">
                  <span className="kpi-label">路线路径点</span>
                  <span className="kpi-text">{scene.metadata.route.length} 个</span>
                </div>
                <div className="kpi-block">
                  <span className="kpi-label">路线生成方式</span>
                  <span className="kpi-text text-amber">
                    {scene.metadata.route_provenance?.status === 'measured'
                      ? '现场实测'
                      : '点云横断面估计'}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Equipment Telemetry Summary */}
          <div className="panel-card">
            <div className="panel-header">
              <div className="header-title">
                <Activity className="text-blue" size={18} />
                <span>监测设备统计</span>
              </div>
              <span className="meta-tag">{scene.devices.length} 台</span>
            </div>
            <div className="panel-body">
              <div className="device-stats-grid">
                {Object.entries(deviceTypeCounts).map(([type, count]) => (
                  <div className="stat-card" key={type}>
                    <span className="stat-name">
                      {DEVICE_TYPE_LABELS[type as keyof typeof DEVICE_TYPE_LABELS] ?? type}
                    </span>
                    <div className="stat-val text-cyan">{count} 台</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Defect Category Chart */}
          <div className="panel-card">
            <div className="panel-header">
              <div className="header-title">
                <AlertCircle className="text-amber" size={18} />
                <span>结构缺陷分类统计</span>
              </div>
            </div>
            <div className="panel-body chart-body">
              <div className="chart-box">
                <ReactECharts
                  option={getDefectDistributionOption()}
                  style={{ height: '100%', width: '100%' }}
                />
              </div>
            </div>
          </div>
        </div>

        {/* CENTER COLUMN: 3D Twin Viewport */}
        <div className="grid-column center-column">
          <div className="panel-card overall-viewport">
            <div className="panel-header">
              <div className="header-title">
                <MapPin className="text-cyan" size={18} />
                <span>三维数字孪生全景</span>
              </div>
              <div className="live-vehicle-pill">
                <Truck size={16} className="text-green" />
                <span>
                  巡检车{speedKmh != null ? ` · ${speedKmh.toFixed(1)} km/h` : '待命'}
                </span>
              </div>
            </div>
            <div className="panel-body viewport-body">
              <TwinViewerAdapter
                sceneMetadata={scene.metadata}
                meshUrl={scene.metadata.mesh_url ?? null}
                pointCloudUrl={scene.metadata.pointcloud_url ?? null}
                displayMode="overlay"
                vehiclePose={vehiclePose}
                trajectory={twinTrajectory}
                sensorDevices={scene.devices}
                detectionEvents={scene.events}
                playbackTime={30}
                selectedObjectId={null}
                cameraCommand={null}
              />
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN */}
        <div className="grid-column right-column">
          {/* 24h Trend Chart */}
          <div className="panel-card">
            <div className="panel-header">
              <div className="header-title">
                <TrendingUp className="text-cyan" size={18} />
                <span>24 小时变形与位移趋势</span>
              </div>
            </div>
            <div className="panel-body chart-body">
              <div className="chart-box">
                <ReactECharts
                  option={get24HourTelemetryOption()}
                  style={{ height: '100%', width: '100%' }}
                />
              </div>
            </div>
          </div>

          {/* Recent Inspection Tasks */}
          <div className="panel-card">
            <div className="panel-header">
              <div className="header-title">
                <Clock className="text-blue" size={18} />
                <span>最新巡检任务</span>
              </div>
            </div>
            <div className="panel-body">
              <div className="list-stack">
                {scene.tasks.map((task) => (
                  <div key={task.id} className="list-item task-mini-item">
                    <div className="item-top">
                      <span className="task-code">{task.id}</span>
                      <span className="task-status-tag">{TASK_STATUS_LABELS[task.status]}</span>
                    </div>
                    <div className="task-title">{task.name}</div>
                    <div className="task-meta">
                      <span>{task.planned_start?.slice(0, 10) ?? '—'}</span>
                      <span>异常候选 {task.event_count ?? 0} 条</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Alert Feed */}
          <div className="panel-card">
            <div className="panel-header">
              <div className="header-title">
                <AlertCircle className="text-red" size={18} />
                <span>异常候选事件</span>
              </div>
              <span className="meta-tag red">{scene.events.length} 条</span>
            </div>
            <div className="panel-body">
              <div className="list-stack">
                {scene.events.map((evt) => (
                  <div key={evt.id} className={`alert-feed-item severity-${evt.severity}`}>
                    <div className="feed-header">
                      <span className="feed-chainage">{evt.id}</span>
                      <span className={`severity-badge ${evt.severity}`}>
                        {severityLabel(evt.severity)}
                      </span>
                    </div>
                    <div className="feed-label">{evt.description ?? evt.type}</div>
                    <div className="feed-time">{evt.detected_at.replace('T', ' ').slice(0, 19)}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
