import React from 'react';
import {
  Server,
  Globe,
  Radio,
  FileUp,
  ArrowRight,
  Cpu,
  Cable,
  MapPin,
  Table2,
  Terminal,
} from 'lucide-react';

const CHANNELS = [
  {
    icon: <Globe size={20} />,
    name: 'HTTP REST 轮询',
    desc: '定时拉取监测网关的批量遥测与台账数据',
    status: '待配置',
  },
  {
    icon: <Radio size={20} />,
    name: 'MQTT 订阅',
    desc: '订阅离层仪、位移计等物联网传感器的实时上报主题',
    status: '待配置',
  },
  {
    icon: <Cable size={20} />,
    name: 'WebSocket 推送',
    desc: '接收车载定位与轨迹的实时广播流',
    status: '待配置',
  },
  {
    icon: <FileUp size={20} />,
    name: '文件导入',
    desc: '导入历史巡检点云、断面扫描与人工记录文件',
    status: '待配置',
  },
];

const FIELD_MAPPINGS = [
  { raw: 'sensor_id', target: '设备编号', unit: '—', note: '主键映射' },
  { raw: 'delam_val', target: '离层位移值', unit: 'mm', note: '量程待确认' },
  { raw: 'disp_val', target: '墙体位移值', unit: 'mm', note: '量程待确认' },
  { raw: 'conv_rate', target: '收敛速率', unit: 'mm/d', note: '采集周期待确认' },
  { raw: 'stress_val', target: '钢筋应力值', unit: 'MPa', note: '单位换算待确认' },
];

const BINDINGS = [
  { code: 'DEV-DEL-01', point: '顶板监测断面 A', status: '待绑定' },
  { code: 'DEV-DISP-01', point: '左侧墙监测断面 B', status: '待绑定' },
  { code: 'DEV-CONV-01', point: '拱顶收敛断面 C', status: '待绑定' },
  { code: 'DEV-STR-01', point: '衬砌应力断面 D', status: '待绑定' },
];

const PREP_LOGS = [
  '接入适配器框架已就绪',
  '四类接入通道已预留，等待甲方协议与数据字典',
  '字段映射表为示例结构，以最终数据字典为准',
];

/**
 * 数据接入准备页 —— 全部为「准备就绪、待配置」的静态展示，不接真数据。
 */
export const DataIngestionPage: React.FC = () => {
  return (
    <div className="page">
      <div className="page-header">
        <h2 className="page-title">数据接入</h2>
        <p className="page-desc">外部监测数据接入的通道规划、字段映射与点位绑定准备</p>
      </div>

      <div className="notice-bar">
        <Cable size={14} />
        <span>甲方协议和数据字典待配置</span>
      </div>

      {/* 数据源通道 */}
      <div className="panel-card">
        <div className="panel-header">
          <div className="header-title">
            <Server className="text-cyan" size={18} />
            <span>接入通道</span>
          </div>
          <span className="meta-tag">{CHANNELS.length} 类通道</span>
        </div>
        <div className="panel-body">
          <div className="channel-grid">
            {CHANNELS.map((ch) => (
              <div key={ch.name} className="channel-card">
                <div className="channel-icon">{ch.icon}</div>
                <div className="channel-name">{ch.name}</div>
                <p className="channel-desc">{ch.desc}</p>
                <span className="status-pill pending">{ch.status}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* RS-485 / Modbus 接入链路示意 */}
      <div className="panel-card">
        <div className="panel-header">
          <div className="header-title">
            <Cpu className="text-cyan" size={18} />
            <span>现场总线接入链路示意（RS-485 / Modbus）</span>
          </div>
          <span className="status-pill pending">待配置</span>
        </div>
        <div className="panel-body">
          <div className="pipeline-diagram">
            <div className="pipeline-node">
              <Cpu size={18} />
              <span className="pipeline-node-title">现场传感器</span>
              <span className="pipeline-node-sub">离层仪 / 位移计 / 收敛计 / 应力计</span>
            </div>
            <div className="pipeline-link">
              <span className="pipeline-link-label">RS-485 · Modbus RTU</span>
              <ArrowRight size={18} />
            </div>
            <div className="pipeline-node">
              <Cable size={18} />
              <span className="pipeline-node-title">边缘网关</span>
              <span className="pipeline-node-sub">协议转换 / 数据缓存 / 断点续传</span>
            </div>
            <div className="pipeline-link">
              <span className="pipeline-link-label">MQTT / HTTPS</span>
              <ArrowRight size={18} />
            </div>
            <div className="pipeline-node">
              <Server size={18} />
              <span className="pipeline-node-title">数字孪生平台</span>
              <span className="pipeline-node-sub">遥测入库 / 三维绑定 / 告警联动</span>
            </div>
          </div>
        </div>
      </div>

      <div className="integration-two-col">
        {/* 字段映射表示意 */}
        <div className="panel-card">
          <div className="panel-header">
            <div className="header-title">
              <Table2 className="text-cyan" size={18} />
              <span>字段映射表（示例）</span>
            </div>
            <span className="meta-tag">以最终数据字典为准</span>
          </div>
          <div className="panel-body">
            <div className="table-responsive">
              <table className="industrial-table">
                <thead>
                  <tr>
                    <th>原始字段</th>
                    <th>平台字段</th>
                    <th>单位</th>
                    <th>备注</th>
                  </tr>
                </thead>
                <tbody>
                  {FIELD_MAPPINGS.map((row) => (
                    <tr key={row.raw}>
                      <td><span className="font-mono telemetry-val">{row.raw}</span></td>
                      <td>{row.target}</td>
                      <td>{row.unit}</td>
                      <td><span className="stat-sub">{row.note}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* 设备编号与三维点位绑定表示意 */}
        <div className="panel-card">
          <div className="panel-header">
            <div className="header-title">
              <MapPin className="text-cyan" size={18} />
              <span>设备编号与三维点位绑定（示例）</span>
            </div>
            <span className="meta-tag">待现场台账确认</span>
          </div>
          <div className="panel-body">
            <div className="table-responsive">
              <table className="industrial-table">
                <thead>
                  <tr>
                    <th>设备编号</th>
                    <th>三维点位</th>
                    <th>绑定状态</th>
                  </tr>
                </thead>
                <tbody>
                  {BINDINGS.map((row) => (
                    <tr key={row.code}>
                      <td><span className="dev-code">{row.code}</span></td>
                      <td>{row.point}</td>
                      <td><span className="status-pill pending">{row.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      {/* 连接状态与日志 */}
      <div className="panel-card">
        <div className="panel-header">
          <div className="header-title">
            <Terminal className="text-cyan" size={18} />
            <span>连接状态与日志</span>
          </div>
          <span className="status-pill pending">未连接 · 待配置</span>
        </div>
        <div className="panel-body">
          <div className="console-logs font-mono">
            {PREP_LOGS.map((log, idx) => (
              <div key={idx} className="log-line">{log}</div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
