import React, { useState, useEffect } from 'react';
import { NavLink } from 'react-router-dom';
import { useDataSource } from '../../services/dataSource';
import {
  Activity,
  Layers,
  Box,
  PlayCircle,
  Cpu,
  CheckSquare,
  Radio,
  Clock,
} from 'lucide-react';

const NAV_ITEMS: Array<{ to: string; label: string; icon: React.ReactNode; end?: boolean }> = [
  { to: '/', label: '综合态势', icon: <Activity size={16} />, end: true },
  { to: '/inspection', label: '三维巡检', icon: <Box size={16} /> },
  { to: '/playback', label: '任务回放', icon: <PlayCircle size={16} /> },
  { to: '/instruments', label: '设备证据与读数', icon: <Cpu size={16} /> },
  { to: '/equipment', label: '监测设备', icon: <Cpu size={16} /> },
  { to: '/review', label: '问题定位与复核', icon: <CheckSquare size={16} /> },
  { to: '/integration', label: '数据接入', icon: <Radio size={16} /> },
];

export const Header: React.FC = () => {
  const { mode, setMode } = useDataSource();
  const [currentTime, setCurrentTime] = useState<string>('');

  useEffect(() => {
    const update = () => {
      const now = new Date();
      setCurrentTime(
        `${now.getFullYear()}-${(now.getMonth() + 1).toString().padStart(2, '0')}-${now.getDate().toString().padStart(2, '0')} ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`
      );
    };
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <header className="global-header">
      <div className="header-branding">
        <div className="logo-icon-box">
          <Layers size={20} />
        </div>
        <h1 className="main-title">Field Inspect</h1>
      </div>

      <nav className="header-nav">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-tab ${isActive ? 'active' : ''}`}
          >
            {item.icon}
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="header-actions">
        <div className="source-switch" role="group" aria-label="数据源切换">
          <button
            className={`source-btn ${mode === 'simulation' ? 'active' : ''}`}
            onClick={() => setMode('simulation')}
          >
            模拟数据
          </button>
          <button
            className={`source-btn ${mode === 'replay' ? 'active' : ''}`}
            onClick={() => setMode('replay')}
          >
            历史回放
          </button>
          <button className="source-btn" disabled title="实时数据通道待配置">
            实时接入
            <span className="pending-tag">待配置</span>
          </button>
        </div>

        <div className="time-display">
          <Clock size={16} />
          <span>{currentTime}</span>
        </div>
      </div>
    </header>
  );
};
