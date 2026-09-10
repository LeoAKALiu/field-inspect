import React, { useEffect, useState } from 'react';
import type { CameraCommand } from '@digital-twin/contracts';
import type { AnomalyReviewItem } from '../types/domain';
import { TwinViewerAdapter } from '../components/TwinViewer/TwinViewerAdapter';
import { useEvents } from '../services/api/hooks';
import { api } from '../services/api/client';
import { eventToAnomalyReviewItem } from '../services/api/adapters';
import { useSceneData } from '../services/scene/sceneData';
import { severityLabel } from '../utils/labels';
import {
  CheckSquare,
  MapPin,
  FileCheck,
  XCircle,
  Send,
  UserCheck,
  CheckCircle,
  Download,
} from 'lucide-react';

export const IssueLocalizationPage: React.FC = () => {
  const { data: apiEvents, loading, error } = useEvents();
  const { data: scene } = useSceneData();
  // API 不可用时降级为本地演示事件（provenance = simulated）
  const events = apiEvents ?? (error && scene ? scene.events : null);

  const [anomalyList, setAnomalyList] = useState<AnomalyReviewItem[]>([]);
  const [selectedAnomaly, setSelectedAnomaly] = useState<AnomalyReviewItem | null>(null);
  const [auditNotes, setAuditNotes] = useState<string>('');
  const [assignedEng, setAssignedEngineer] = useState<string>('李工（监测组）');
  const [showSuccessToast, setShowSuccessToast] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [cameraCommand, setCameraCommand] = useState<CameraCommand | null>(null);

  // Adapt events into the review-item shape once they arrive.
  useEffect(() => {
    if (!events) return;
    const items = events.map(eventToAnomalyReviewItem);
    setAnomalyList(items);
    setSelectedAnomaly((prev) => {
      if (prev) {
        const stillPresent = items.find((i) => i.id === prev.id);
        if (stillPresent) return stillPresent;
      }
      return items[0] ?? null;
    });
  }, [events]);

  useEffect(() => {
    setAuditNotes(selectedAnomaly?.notes || '');
    setAssignedEngineer(selectedAnomaly?.assignedEngineer || '李工（监测组）');
  }, [selectedAnomaly?.id]);

  const rawStatus = (selectedAnomaly?.meta as Record<string, unknown> | undefined)?.rawStatus as
    | string
    | undefined;
  const canAcknowledge = rawStatus === 'open';
  const canFalsePositive = rawStatus === 'acknowledged';

  const selectedEvent = events?.find((e) => e.id === selectedAnomaly?.id) ?? null;

  const handleSelect = (ano: AnomalyReviewItem) => {
    setSelectedAnomaly(ano);
    setCameraCommand({ type: 'focusObject', objectId: ano.id });
  };

  const handleUpdateVerdict = async (status: 'verified' | 'false_positive') => {
    if (!selectedAnomaly) return;
    setIsSubmitting(true);
    try {
      const backendStatus = status === 'verified' ? 'acknowledged' : 'false_positive';
      const updated = await api.updateEventStatus(selectedAnomaly.id, {
        status: backendStatus,
        comment: auditNotes,
        handled_by: assignedEng,
      });
      const mapped = eventToAnomalyReviewItem(updated);
      setAnomalyList((prev) => prev.map((item) => (item.id === mapped.id ? mapped : item)));
      setSelectedAnomaly(mapped);
      setShowSuccessToast(`问题 ${mapped.id} 已更新为：${mapped.reviewStatusName}`);
    } catch {
      setShowSuccessToast('状态更新失败，请稍后重试');
    } finally {
      setIsSubmitting(false);
      setTimeout(() => setShowSuccessToast(null), 3000);
    }
  };

  const handleExport = async () => {
    try {
      const blob = await api.exportEvents({ format: 'csv' });
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = `events-${Date.now()}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      setShowSuccessToast('导出失败，请稍后重试');
      setTimeout(() => setShowSuccessToast(null), 3000);
    }
  };

  return (
    <div className="page">
      {/* Toast Notification */}
      {showSuccessToast && (
        <div className="toast-notification">
          <CheckCircle size={16} className="text-green" />
          <span>{showSuccessToast}</span>
        </div>
      )}

      <div className="page-header">
        <h2 className="page-title">问题定位与复核</h2>
        <p className="page-desc">对识别出的异常候选进行三维定位、专家复核与派单处理</p>
      </div>

      <div className="localization-page-grid">
        {/* Left Column: Anomaly Candidates Queue */}
        <div className="panel-card">
          <div className="panel-header">
            <div className="header-title">
              <CheckSquare className="text-cyan" size={18} />
              <span>待复核队列</span>
            </div>
            <div className="header-title" style={{ gap: 8 }}>
              <span className="meta-tag red">{anomalyList.length} 项</span>
              <button className="hud-action-btn" onClick={handleExport} title="导出事件记录">
                <Download size={14} />
                <span>导出记录</span>
              </button>
            </div>
          </div>

          <div className="panel-body">
            {loading && <div className="no-select-tip"><p>正在加载异常事件…</p></div>}
            <div className="list-stack">
              {anomalyList.map((ano) => {
                const isSelected = selectedAnomaly?.id === ano.id;
                return (
                  <div
                    key={ano.id}
                    className={`anomaly-queue-item ${isSelected ? 'selected' : ''}`}
                    onClick={() => handleSelect(ano)}
                  >
                    <div className="queue-item-top">
                      <span className="ano-code">{ano.id}</span>
                      <span className={`severity-badge ${ano.severity}`}>{severityLabel(ano.severity)}</span>
                    </div>
                    <div className="ano-title">{ano.label}</div>
                    <div className="queue-item-bottom">
                      <span className="chainage-text">
                        <MapPin size={13} /> {ano.chainage}
                      </span>
                      <span className={`status-pill ${ano.reviewStatus}`}>
                        {ano.reviewStatusName}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Center Column: 3D Spatial Pinpoint View */}
        <div className="panel-card localization-viewport">
          <div className="panel-header">
            <div className="header-title">
              <MapPin className="text-cyan" size={18} />
              <span>三维剖面精确定位</span>
            </div>
            {selectedAnomaly && (
              <span className="meta-tag amber">识别可信度 {(selectedAnomaly.confidence * 100).toFixed(0)}%</span>
            )}
          </div>
          <div className="panel-body viewport-body">
            {scene && (
              <TwinViewerAdapter
                sceneMetadata={scene.metadata}
                meshUrl={scene.metadata.mesh_url ?? null}
                pointCloudUrl={scene.metadata.pointcloud_url ?? null}
                displayMode="overlay"
                vehiclePose={null}
                trajectory={[]}
                sensorDevices={[]}
                detectionEvents={selectedEvent ? [selectedEvent] : []}
                playbackTime={0}
                selectedObjectId={selectedAnomaly?.id ?? null}
                cameraCommand={cameraCommand}
              />
            )}
          </div>
        </div>

        {/* Right Column: Audit Verification Form */}
        <div className="panel-card">
          <div className="panel-header">
            <div className="header-title">
              <FileCheck className="text-cyan" size={18} />
              <span>复核与派单</span>
            </div>
          </div>

          <div className="panel-body">
            {selectedAnomaly && (
              <div className="audit-form">
                <div className="form-group">
                  <label className="form-label">缺陷编号与属性</label>
                  <div className="form-readonly-val">
                    {selectedAnomaly.id} · {selectedAnomaly.defectTypeName}
                  </div>
                </div>

                <div className="form-group">
                  <label className="form-label">指派核查工程师</label>
                  <div className="input-with-icon">
                    <UserCheck size={16} className="input-icon" />
                    <input
                      type="text"
                      value={assignedEng}
                      onChange={(e) => setAssignedEngineer(e.target.value)}
                      className="form-input"
                    />
                  </div>
                </div>

                <div className="form-group">
                  <label className="form-label">现场核查与工程意见</label>
                  <textarea
                    rows={4}
                    value={auditNotes}
                    onChange={(e) => setAuditNotes(e.target.value)}
                    className="form-textarea"
                    placeholder="请输入复核结论与养护派单建议"
                  />
                </div>

                <div className="action-buttons-group">
                  <button
                    className="audit-btn btn-success"
                    onClick={() => handleUpdateVerdict('verified')}
                    disabled={!canAcknowledge || isSubmitting}
                    title={canAcknowledge ? undefined : '仅待复核的事件可执行派单'}
                  >
                    <Send size={16} />
                    <span>确认缺陷并派单</span>
                  </button>

                  <button
                    className="audit-btn btn-danger"
                    onClick={() => handleUpdateVerdict('false_positive')}
                    disabled={!canFalsePositive || isSubmitting}
                    title={canFalsePositive ? undefined : '仅已确认的事件可判定为误报'}
                  >
                    <XCircle size={16} />
                    <span>判定为误报</span>
                  </button>
                </div>

                <div className="audit-history-box">
                  <div className="hist-header">处理记录</div>
                  <div className="hist-timeline">
                    <div className="hist-item">
                      <span className="hist-time">{selectedAnomaly.detectedAt.replace('T', ' ').slice(0, 19)}</span>
                      <span className="hist-desc">巡检车自动捕捉异常标注</span>
                    </div>
                    <div className="hist-item">
                      <span className="hist-time">当前状态</span>
                      <span className="hist-desc text-cyan">{selectedAnomaly.reviewStatusName}</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
