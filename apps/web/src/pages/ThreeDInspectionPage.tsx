import React, { useMemo, useState } from 'react';
import type { CameraCommand, DetectionEvent, SensorDevice } from '@digital-twin/contracts';
import { useSceneData } from '../services/scene/sceneData';
import { sampleTrajectoryAt, toTwinTrajectory } from '../services/mock/sceneFallback';
import { TwinViewerAdapter } from '../components/TwinViewer/TwinViewerAdapter';
import { DataLineage } from '../components/Common/DataLineage';
import { DEVICE_TYPE_LABELS, severityLabel } from '../utils/labels';
import { EVENT_TYPE_LABELS } from '../services/api/adapters';
import {
  Truck,
  Compass,
  Layers,
  MapPin,
  AlertTriangle,
  Info,
  CheckCircle2,
} from 'lucide-react';

type SelectedEntity =
  | { kind: 'event'; event: DetectionEvent }
  | { kind: 'device'; device: SensorDevice };

export const ThreeDInspectionPage: React.FC = () => {
  const { data: scene, loading } = useSceneData();
  const [selected, setSelected] = useState<SelectedEntity | null>(null);
  const [cameraCommand, setCameraCommand] = useState<CameraCommand | null>({
    type: 'focusObject',
    objectId: 'inspection-vehicle',
  });

  const twinTrajectory = useMemo(
    () => (scene ? toTwinTrajectory(scene.trajectory) : []),
    [scene],
  );

  const vehicleSample = scene ? sampleTrajectoryAt(scene.trajectory, 28) : null;
  const vehiclePose = vehicleSample
    ? { position: vehicleSample.position, heading_deg: vehicleSample.heading_deg ?? 0 }
    : null;

  if (loading || !scene) {
    return (
      <div className="page">
        <div className="page-header">
          <h2 className="page-title">三维巡检</h2>
          <p className="page-desc">在三维场景中查看巡检车状态、定位设备与异常候选</p>
        </div>
        <div className="no-select-tip"><p>正在加载场景数据…</p></div>
      </div>
    );
  }

  const focusEntity = (entity: SelectedEntity) => {
    setSelected(entity);
    setCameraCommand({
      type: 'focusObject',
      objectId: entity.kind === 'event' ? entity.event.id : entity.device.id,
    });
  };

  const firstDevice = scene.devices[0] ?? null;

  return (
    <div className="page">
      <div className="page-header">
        <h2 className="page-title">三维巡检</h2>
        <p className="page-desc">在三维场景中查看巡检车状态、定位设备与异常候选</p>
      </div>

      <div className="inspection-layout">
        {/* Full Area 3D Digital Twin Canvas */}
        <div className="inspection-canvas-wrapper">
          <TwinViewerAdapter
            sceneMetadata={scene.metadata}
            meshUrl={scene.metadata.mesh_url ?? null}
            pointCloudUrl={scene.metadata.pointcloud_url ?? null}
            displayMode="overlay"
            vehiclePose={vehiclePose}
            trajectory={twinTrajectory}
            sensorDevices={scene.devices}
            detectionEvents={scene.events}
            playbackTime={28}
            selectedObjectId={
              selected ? (selected.kind === 'event' ? selected.event.id : selected.device.id) : null
            }
            cameraCommand={cameraCommand}
            onObjectSelect={(e) => {
              if (e.kind === 'event') {
                const found = scene.events.find((a) => a.id === e.id);
                if (found) setSelected({ kind: 'event', event: found });
              } else if (e.kind === 'device') {
                const found = scene.devices.find((d) => d.id === e.id);
                if (found) setSelected({ kind: 'device', device: found });
              }
            }}
          />
        </div>

        {/* Floating Left Drawer: Telemetry & Camera Presets */}
        <div className="floating-panel left-panel">
          <div className="panel-card glass-card">
            <div className="panel-header">
              <div className="header-title">
                <Truck className="text-cyan" size={18} />
                <span>巡检车状态</span>
              </div>
              <span className="status-badge live">模拟轨迹</span>
            </div>
            <div className="panel-body">
              <div className="telemetry-grid">
                <div className="tel-item">
                  <span className="tel-label">巡检车编号</span>
                  <span className="tel-val">DT-INSPECT-V01</span>
                </div>
                <div className="tel-item">
                  <span className="tel-label">车速</span>
                  <span className="tel-val text-cyan">
                    {vehicleSample?.speed_mps != null
                      ? `${(vehicleSample.speed_mps * 3.6).toFixed(1)} km/h`
                      : '—'}
                  </span>
                </div>
                <div className="tel-item">
                  <span className="tel-label">当前位置 X</span>
                  <span className="tel-val text-amber">
                    {vehicleSample ? `${vehicleSample.position.x.toFixed(1)} m` : '—'}
                  </span>
                </div>
                <div className="tel-item">
                  <span className="tel-label">航向角</span>
                  <span className="tel-val">
                    {vehicleSample?.heading_deg != null
                      ? `${vehicleSample.heading_deg.toFixed(0)}°`
                      : '—'}
                  </span>
                </div>
                <div className="tel-item">
                  <span className="tel-label">空间绑定</span>
                  <span className="tel-val text-cyan">点云估计路线</span>
                </div>
                <div className="tel-item">
                  <span className="tel-label">坐标框架</span>
                  <span className="tel-val">scene_local_yup</span>
                </div>
              </div>
            </div>
          </div>

          {/* Quick Camera Presets */}
          <div className="panel-card glass-card">
            <div className="panel-header">
              <div className="header-title">
                <Compass className="text-blue" size={18} />
                <span>视角快速定位</span>
              </div>
            </div>
            <div className="panel-body">
              <div className="camera-preset-buttons">
                {scene.events.slice(0, 2).map((evt) => (
                  <button
                    key={evt.id}
                    className="preset-btn"
                    onClick={() => focusEntity({ kind: 'event', event: evt })}
                  >
                    {evt.severity === 'high' || evt.severity === 'critical' ? (
                      <AlertTriangle size={16} className="text-red" />
                    ) : (
                      <MapPin size={16} className="text-amber" />
                    )}
                    <span>{EVENT_TYPE_LABELS[evt.type] ?? evt.type} · {evt.id}</span>
                  </button>
                ))}
                {firstDevice && (
                  <button
                    className="preset-btn"
                    onClick={() => focusEntity({ kind: 'device', device: firstDevice })}
                  >
                    <Layers size={16} className="text-cyan" />
                    <span>{firstDevice.name}</span>
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Floating Right Drawer: Focus Inspector Details */}
        <div className="floating-panel right-panel">
          <div className="panel-card glass-card">
            <div className="panel-header">
              <div className="header-title">
                <Info className="text-cyan" size={18} />
                <span>选中目标属性</span>
              </div>
              {selected && (
                <span className="meta-tag blue">
                  {selected.kind === 'event' ? selected.event.id : selected.device.id}
                </span>
              )}
            </div>
            <div className="panel-body">
              {selected ? (
                <div className="entity-detail-box">
                  {selected.kind === 'event' ? (
                    <>
                      <div className="detail-title-row">
                        <h4 className="detail-title">
                          {selected.event.description ?? EVENT_TYPE_LABELS[selected.event.type]}
                        </h4>
                        <span className={`severity-badge ${selected.event.severity}`}>
                          {severityLabel(selected.event.severity)}
                        </span>
                      </div>
                      <div className="detail-table">
                        <div className="detail-row">
                          <span className="row-key">缺陷类型</span>
                          <span className="row-val">{EVENT_TYPE_LABELS[selected.event.type]}</span>
                        </div>
                        <div className="detail-row">
                          <span className="row-key">空间位置 X/Y/Z</span>
                          <span className="row-val font-mono">
                            {selected.event.position.x.toFixed(1)} / {selected.event.position.y.toFixed(1)} / {selected.event.position.z.toFixed(1)}
                          </span>
                        </div>
                        {selected.event.confidence != null && (
                          <div className="detail-row">
                            <span className="row-key">识别可信度</span>
                            <span className="row-val text-green">
                              {(selected.event.confidence * 100).toFixed(1)}%
                            </span>
                          </div>
                        )}
                        <div className="detail-row">
                          <span className="row-key">发现时间</span>
                          <span className="row-val">
                            {selected.event.detected_at.replace('T', ' ').slice(0, 19)}
                          </span>
                        </div>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="detail-title-row">
                        <h4 className="detail-title">{selected.device.name}</h4>
                        <span className={`status-badge-sm ${selected.device.status}`}>
                          {selected.device.status === 'online' ? '在线' : '离线'}
                        </span>
                      </div>
                      <div className="detail-table">
                        <div className="detail-row">
                          <span className="row-key">设备类型</span>
                          <span className="row-val">{DEVICE_TYPE_LABELS[selected.device.type]}</span>
                        </div>
                        <div className="detail-row">
                          <span className="row-key">测量单位</span>
                          <span className="row-val">{selected.device.unit}</span>
                        </div>
                        {selected.device.position && (
                          <div className="detail-row">
                            <span className="row-key">空间位置 X/Y/Z</span>
                            <span className="row-val font-mono">
                              {selected.device.position.x.toFixed(1)} / {selected.device.position.y.toFixed(1)} / {selected.device.position.z.toFixed(1)}
                            </span>
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <>
                  <div className="no-select-tip compact">
                    <CheckCircle2 size={24} className="text-gray" />
                    <p>点击三维场景中的设备或异常标注查看属性</p>
                  </div>
                  <DataLineage
                    variant="detail"
                    routeProvenance={scene.metadata.route_provenance}
                  />
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
