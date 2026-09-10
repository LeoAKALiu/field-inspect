import React, { useState } from 'react';
import ReactECharts from 'echarts-for-react';
import type { CameraCommand, SensorDevice } from '@digital-twin/contracts';
import { TwinViewerAdapter } from '../components/TwinViewer/TwinViewerAdapter';
import { useSceneData } from '../services/scene/sceneData';
import { useDevices, useDeviceReadings } from '../services/api/hooks';
import { readingsToLineOption } from '../services/api/adapters';
import { DEVICE_TYPE_LABELS } from '../utils/labels';
import {
  Cpu,
  Search,
  Filter,
  Activity,
  Sliders,
  Cable,
} from 'lucide-react';

export const EquipmentMonitoringPage: React.FC = () => {
  const { data: scene, loading } = useSceneData();
  const [filterKind, setFilterKind] = useState<string>('all');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [cameraCommand, setCameraCommand] = useState<CameraCommand | null>(null);

  // 环境量遥测曲线（后端模拟读数，与结构监测台账相互独立）
  const { data: backendDevices } = useDevices();
  const connectedDevice = backendDevices?.[0] ?? null;
  const { data: readings, loading: readingsLoading } = useDeviceReadings(
    connectedDevice?.id ?? null,
  );

  if (loading || !scene) {
    return (
      <div className="page">
        <div className="page-header">
          <h2 className="page-title">监测设备</h2>
          <p className="page-desc">结构监测传感器的在线状态、遥测指标与空间分布</p>
        </div>
        <div className="no-select-tip"><p>正在加载场景数据…</p></div>
      </div>
    );
  }

  const devices = scene.devices;
  const filteredDevices = devices.filter((dev) => {
    if (filterKind !== 'all' && dev.type !== filterKind) return false;
    if (filterStatus !== 'all' && dev.status !== filterStatus) return false;
    if (
      searchQuery &&
      !dev.name.toLowerCase().includes(searchQuery.toLowerCase()) &&
      !dev.id.toLowerCase().includes(searchQuery.toLowerCase())
    )
      return false;
    return true;
  });

  const selectedDevice: SensorDevice | null =
    devices.find((d) => d.id === selectedId) ?? filteredDevices[0] ?? devices[0] ?? null;

  const handleSelect = (dev: SensorDevice) => {
    setSelectedId(dev.id);
    setCameraCommand({ type: 'focusObject', objectId: dev.id });
  };

  return (
    <div className="page">
      <div className="page-header">
        <h2 className="page-title">监测设备</h2>
        <p className="page-desc">结构监测传感器的在线状态、遥测指标与空间分布</p>
      </div>

      {/* 接入边界说明条 */}
      <div className="notice-bar">
        <Cable size={14} />
        <span>系统侧已准备好接入离层仪、位移计等外部数据；甲方设备协议和数据字典待配置</span>
      </div>

      <div className="equipment-page-grid">
        {/* Left Column: Equipment Management Table */}
        <div className="panel-card">
          <div className="panel-header">
            <div className="header-title">
              <Cpu className="text-cyan" size={18} />
              <span>设备台账与在线状态</span>
            </div>
            <span className="meta-tag blue">共 {filteredDevices.length} 台</span>
          </div>

          <div className="panel-body">
            {/* Filter Controls Bar */}
            <div className="equipment-filter-bar">
              <div className="search-input-box">
                <Search size={16} className="search-icon" />
                <input
                  type="text"
                  placeholder="搜索设备编号或名称"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="search-input"
                />
              </div>

              <div className="filter-select-group">
                <Filter size={16} className="text-gray" />
                <select
                  value={filterKind}
                  onChange={(e) => setFilterKind(e.target.value)}
                  className="filter-select"
                >
                  <option value="all">所有设备类型</option>
                  <option value="delamination">离层仪</option>
                  <option value="displacement">位移计</option>
                  <option value="convergence">收敛计</option>
                  <option value="stress">应力计</option>
                </select>

                <select
                  value={filterStatus}
                  onChange={(e) => setFilterStatus(e.target.value)}
                  className="filter-select"
                >
                  <option value="all">所有状态</option>
                  <option value="online">在线</option>
                  <option value="offline">离线</option>
                </select>
              </div>
            </div>

            {/* Table */}
            <div className="table-responsive">
              <table className="industrial-table">
                <thead>
                  <tr>
                    <th>设备编号 / 名称</th>
                    <th>类型</th>
                    <th>空间位置</th>
                    <th>状态</th>
                    <th>单位</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredDevices.map((dev) => {
                    const isSelected = selectedDevice?.id === dev.id;
                    return (
                      <tr
                        key={dev.id}
                        className={`table-row ${isSelected ? 'selected' : ''}`}
                        onClick={() => handleSelect(dev)}
                      >
                        <td>
                          <div className="dev-name-box">
                            <span className="dev-code">{dev.id}</span>
                            <span className="dev-label">{dev.name}</span>
                          </div>
                        </td>
                        <td>
                          <span className="kind-tag">{DEVICE_TYPE_LABELS[dev.type]}</span>
                        </td>
                        <td>
                          <span className="chainage-tag">
                            {dev.position
                              ? `${dev.position.x.toFixed(1)} / ${dev.position.y.toFixed(1)} / ${dev.position.z.toFixed(1)}`
                              : '未绑定'}
                          </span>
                        </td>
                        <td>
                          <span className={`status-badge-sm ${dev.status}`}>
                            {dev.status === 'online' ? '在线' : '离线'}
                          </span>
                        </td>
                        <td>
                          <span className="telemetry-val">{dev.unit}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Right Column: Selected Device Deep Dive */}
        <div className="equipment-right-col">
          {/* Device Detail Card */}
          <div className="panel-card">
            <div className="panel-header">
              <div className="header-title">
                <Activity className="text-cyan" size={18} />
                <span>遥测指标与历史趋势</span>
              </div>
              {selectedDevice && <span className="meta-tag">{selectedDevice.id}</span>}
            </div>
            <div className="panel-body">
              {selectedDevice && (
                <>
                  <div className="dev-info-header">
                    <h3>{selectedDevice.name}</h3>
                    <p className="subtitle">
                      {DEVICE_TYPE_LABELS[selectedDevice.type]} ·{' '}
                      {selectedDevice.status === 'online' ? '在线' : '离线'}
                    </p>
                  </div>

                  <div className="dev-metrics-pills">
                    <div className="pill">
                      <span className="label">传感器类型</span>
                      <span className="val">{DEVICE_TYPE_LABELS[selectedDevice.type]}</span>
                    </div>
                    <div className="pill">
                      <span className="label">测量单位</span>
                      <span className="val text-cyan">{selectedDevice.unit}</span>
                    </div>
                  </div>
                </>
              )}

              {/* ECharts History Trend — 后端模拟读数 */}
              <div className="chart-box">
                {readingsLoading && <p className="page-desc">正在加载遥测数据…</p>}
                {!readingsLoading && connectedDevice && readings && (
                  <ReactECharts
                    option={readingsToLineOption(
                      readings,
                      `${connectedDevice.name} (${connectedDevice.unit})`,
                    )}
                    style={{ height: '100%', width: '100%' }}
                  />
                )}
                {!readingsLoading && !connectedDevice && (
                  <p className="page-desc">暂无可用设备遥测数据</p>
                )}
              </div>
            </div>
          </div>

          {/* Device 3D Spatial View */}
          <div className="panel-card equipment-viewport">
            <div className="panel-header">
              <div className="header-title">
                <Sliders className="text-blue" size={18} />
                <span>三维空间定位</span>
              </div>
            </div>
            <div className="panel-body viewport-body">
              <TwinViewerAdapter
                sceneMetadata={scene.metadata}
                meshUrl={scene.metadata.mesh_url ?? null}
                pointCloudUrl={scene.metadata.pointcloud_url ?? null}
                displayMode="mesh"
                vehiclePose={null}
                trajectory={[]}
                sensorDevices={selectedDevice ? [selectedDevice] : []}
                detectionEvents={[]}
                playbackTime={0}
                selectedObjectId={selectedDevice?.id ?? null}
                cameraCommand={cameraCommand}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
