import { RunAssetEvidence, useRunAssets } from '../components/RunAssetEvidence';
import { RunStationPanel } from '../components/RunStationPanel';
import { RunLocalizationPanel } from '../components/RunLocalizationPanel';
import { localizationObjectId, type CameraCommand } from '@digital-twin/contracts';
import React, { useEffect, useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { getMultiChannelPlaybackOption } from '../services/mock/mockTelemetryData';
import {
  useBoundSceneMetadata,
  useDisplayTrajectory,
  useSceneData,
  useTaskTrajectory,
} from '../services/scene/sceneData';
import {
  nearestTrajectoryTime,
  sampleTrajectoryAt,
  toTwinTrajectory,
} from '../services/mock/sceneFallback';
import { TwinViewerAdapter } from '../components/TwinViewer/TwinViewerAdapter';
import { recordedRunLabel, severityLabel } from '../utils/labels';
import { useRecordedPointCloud } from '../services/scene/useRecordedPointCloud';
import { useRunLocalizations, type MarkerLocalization } from '../services/scene/useRunLocalizations';
import { useTasks } from '../services/api/hooks';
import {
  Play,
  Pause,
  RotateCcw,
  FastForward,
  Clock,
  Video,
  List,
  MapPin,
} from 'lucide-react';

export const TaskPlaybackPage: React.FC = () => {
  const { data: scene, loading } = useSceneData();
  const { data: tasks, loading: tasksLoading, error: tasksError } = useTasks();
  const [catalog, setCatalog] = useState<'recorded' | 'demonstration'>('recorded');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState<boolean>(true);
  const [playbackTime, setPlaybackTime] = useState<number>(0);
  const [speedMultiplier, setSpeedMultiplier] = useState<number>(1);

  const recordedTasks = useMemo(
    () =>
      (tasks ?? [])
        .filter((task) => task.run_kind === 'recorded')
        .slice()
        .sort((a, b) => (b.actual_end ?? '').localeCompare(a.actual_end ?? '')),
    [tasks],
  );
  const demonstrationTasks = useMemo(
    () => (scene?.tasks ?? []).filter((task) => task.run_kind !== 'recorded'),
    [scene?.tasks],
  );
  const catalogTasks = catalog === 'recorded' ? recordedTasks : demonstrationTasks;
  const selectedTask =
    catalogTasks.find((task) => task.id === selectedTaskId) ?? catalogTasks[0] ?? null;

  const [assetSelection, setAssetSelection] = useState<{ runId: string; assetId: string; at: string } | null>(null);
  const { devices: registeredDevices, error: assetError } = useRunAssets(selectedTask?.scene_id, selectedTask?.scene_version_id);
  const isRecorded = selectedTask?.run_kind === 'recorded';
  const pointCloud = useRecordedPointCloud(isRecorded && selectedTask?.has_pointcloud ? selectedTask.id : null);
  const localizations = useRunLocalizations(isRecorded ? selectedTask.id : null, selectedTask?.scene_version_id, selectedTask?.alignment_id);
  const [localizationSelection, setLocalizationSelection] = useState<{ runId: string; id: string; camera: CameraCommand | null } | null>(null);
  const [viewerError, setViewerError] = useState<{ binding: string; message: string } | null>(null);
  const viewerBinding = JSON.stringify([selectedTask?.id, selectedTask?.scene_version_id, pointCloud.url]);
  const selectedLocalization = localizationSelection?.runId === selectedTask?.id
    ? localizations.rows.find(entry => entry.localization.localization_id === localizationSelection?.id) : undefined;
  const viewerLocalizations = useMemo(() => localizations.rows.map(entry => entry.localization), [localizations.rows]);
  const { data: boundMetadata, loading: boundLoading } = useBoundSceneMetadata(
    isRecorded && selectedTask?.scene_version_id ? selectedTask.scene_id : null,
    isRecorded ? (selectedTask?.scene_version_id ?? null) : null,
  );
  const playbackMetadata = isRecorded ? boundMetadata : (scene?.metadata ?? null);
  const fillFromSceneRoute = !isRecorded;
  const { data: trajectory } = useTaskTrajectory(
    selectedTask?.id ?? null,
    playbackMetadata,
    { fillFromSceneRoute },
  );
  const { data: displayPoints, disclaimer } = useDisplayTrajectory(
    selectedTask?.id ?? null,
    playbackMetadata,
    { fillFromSceneRoute },
  );

  const drawingPoints = displayPoints.length > 0 ? displayPoints : isRecorded ? trajectory : displayPoints;
  const twinTrajectory = useMemo(() => toTwinTrajectory(drawingPoints), [drawingPoints]);
  const replayTimes = useMemo(() => toTwinTrajectory(trajectory), [trajectory]);

  const maxDuration =
    replayTimes.length > 1
      ? replayTimes[replayTimes.length - 1]!.time
      : isRecorded || replayTimes.length === 0
        ? 0
        : 120;

  function selectLocalization(entry: MarkerLocalization, focus: boolean) {
    if (!selectedTask || entry.localization.run_id !== selectedTask.id) return;
    setIsPlaying(false);
    setAssetSelection(null);
    setLocalizationSelection({ runId: selectedTask.id, id: entry.localization.localization_id,
      camera: focus && entry.localization.position ? { type: 'focusObject', objectId: localizationObjectId(entry.localization.localization_id) } : null });
    const seconds = trajectory[0] ? (Date.parse(entry.captured_at) - Date.parse(trajectory[0].timestamp)) / 1000 : NaN;
    if (Number.isFinite(seconds) && seconds >= 0 && seconds <= maxDuration) setPlaybackTime(seconds);
  }

  // 任务切换后回到起点
  useEffect(() => {
    setPlaybackTime(0);
  }, [selectedTask?.id]);

  // Playback timer loop
  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    if (isPlaying) {
      interval = setInterval(() => {
        setPlaybackTime((prev) => {
          if (prev >= maxDuration) return 0;
          return prev + 1 * speedMultiplier;
        });
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [isPlaying, speedMultiplier, maxDuration]);

  const formatTime = (sec: number) => {
    const mins = Math.floor(sec / 60);
    const secs = Math.floor(sec % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  if (loading || !scene) {
    return (
      <div className="page">
        <div className="page-header">
          <h2 className="page-title">任务回放</h2>
          <p className="page-desc">回放历史巡检任务的三维轨迹与多通道监测波形</p>
        </div>
        <div className="no-select-tip"><p>正在加载场景数据…</p></div>
      </div>
    );
  }

  const vehicleSample = sampleTrajectoryAt(trajectory, playbackTime);
  const vehiclePose = vehicleSample
    ? { position: vehicleSample.position, heading_deg: vehicleSample.heading_deg ?? 0 }
    : null;

  const displayEvents = scene.events.filter(
    (event) => selectedTask != null && event.task_id === selectedTask.id,
  );
  const showRecordedEmpty = catalog === 'recorded' && recordedTasks.length === 0;
  const recordedWaitingBound = Boolean(isRecorded && selectedTask && (boundLoading || !boundMetadata));
  const recordedBoundFailed = Boolean(
    isRecorded && selectedTask && !boundLoading && !boundMetadata,
  );

  return (
    <div className="page">
      <div className="page-header">
        <h2 className="page-title">任务回放</h2>
        <p className="page-desc">现场实录与演示运行分开回放；导入成功不会自动变成现场已验收</p>
      </div>

      <div className="playback-top-bar">
        {catalog === 'recorded' && recordedTasks.length === 0 ? (
          <div className="recorded-empty" data-testid="recorded-run-empty">
            <span>{tasksLoading ? '正在读取现场实录运行…' : tasksError ? '现场实录运行列表读取失败，请核对服务与授权后刷新。' : '暂无现场实录运行'}</span>
            <button
              type="button"
              className="demo-entry-btn"
              data-testid="show-demonstration-runs"
              onClick={() => {
                setCatalog('demonstration');
                setSelectedTaskId(null);
              }}
            >
              查看演示运行
            </button>
          </div>
        ) : (
          <>
            <div className="task-select-wrapper">
              <Video className="text-cyan" size={18} />
              <span>{catalog === 'recorded' ? '现场实录' : '演示运行'}</span>
              <select
                value={selectedTask?.id ?? ''}
                onChange={(e) => setSelectedTaskId(e.target.value)}
                className="task-select"
              >
                {catalogTasks.map((task) => (
                  <option key={task.id} value={task.id}>
                    {task.name}
                  </option>
                ))}
              </select>
              {catalog === 'recorded' ? (
                <button
                  type="button"
                  className="demo-entry-btn"
                  data-testid="show-demonstration-runs"
                  onClick={() => {
                    setCatalog('demonstration');
                    setSelectedTaskId(null);
                  }}
                >
                  查看演示运行
                </button>
              ) : (
                <button
                  type="button"
                  className="demo-entry-btn"
                  data-testid="show-recorded-runs"
                  onClick={() => {
                    setCatalog('recorded');
                    setSelectedTaskId(null);
                  }}
                >
                  返回现场实录
                </button>
              )}
            </div>

            {selectedTask && (
              <div className="task-meta-pills">
                <div className="meta-pill" data-testid="run-kind-label">
                  <span className="key">类型</span>
                  <span className="val">{recordedRunLabel(selectedTask)}</span>
                </div>
                {selectedTask.package_status === 'completed_with_exceptions' && (
                  <div className="meta-pill" data-testid="package-status-label">
                    <span className="key">包状态</span>
                    <span className="val">完成但有例外</span>
                  </div>
                )}
                {selectedTask.run_kind === 'recorded' && selectedTask.scene_version_id && (
                  <div className="meta-pill" data-testid="scene-version-label">
                    <span className="key">场景版本</span>
                    <span className="val">{selectedTask.scene_version_id}</span>
                  </div>
                )}
                {isRecorded && boundMetadata?.asset_sha256 && (
                  <div className="meta-pill" data-testid="scene-version-binding">
                    <span className="key">场景资产</span>
                    <span className="val">{boundMetadata.asset_sha256?.slice(0, 12)}</span>
                  </div>
                )}
                {selectedTask.run_kind === 'recorded' && selectedTask.acceptance_summary && (
                  <div className="meta-pill" data-testid="acceptance-summary">
                    <span className="key">验收汇总</span>
                    <span className="val">
                      必检 {selectedTask.acceptance_summary.required_passed}/
                      {selectedTask.acceptance_summary.required_total} 通过
                      {selectedTask.acceptance_summary.auxiliary_failed > 0
                        ? `，辅助超限 ${selectedTask.acceptance_summary.auxiliary_failed}`
                        : '，辅助未超限'}
                    </span>
                  </div>
                )}
                {selectedTask.run_kind === 'recorded' && selectedTask.acceptance_recorded_at && (
                  <div className="meta-pill" data-testid="acceptance-time">
                    <span className="key">
                      {selectedTask.acceptance_state === 'withdrawn' ? '撤销时间' : '验收时间'}
                    </span>
                    <span className="val">{selectedTask.acceptance_recorded_at}</span>
                  </div>
                )}
                <div className="meta-pill" data-testid="run-identity">
                  <span className="key">
                    {selectedTask.run_kind === 'recorded' ? '运行身份' : '演示运行'}
                  </span>
                  <span className="val">{selectedTask.id}</span>
                </div>
                <div className="meta-pill">
                  <span className="key">里程</span>
                  <span className="val">{selectedTask.distance_m ?? '—'} m</span>
                </div>
                <div className="meta-pill">
                  <span className="key">异常候选</span>
                  <span className="val text-amber">{selectedTask.event_count ?? 0} 条</span>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Main Grid: 3D Replay + Multi-channel Waveform + Event Log */}
      {showRecordedEmpty ? null : (
      <div className="playback-main-body">
        {/* Left: 3D Replay & Waveform Chart */}
        <div className="playback-left-col">
          <div className="panel-card playback-viewport">
            <div className="panel-header">
              <div className="header-title">
                <Video className="text-cyan" size={18} />
                <span>三维场景同步回放</span>
              </div>
              <span className="meta-tag cyan">
                回放进度 {maxDuration > 0 ? Math.floor((playbackTime / maxDuration) * 100) : 0}%
              </span>
            </div>
            <div className="panel-body viewport-body">
              {recordedBoundFailed ? (
                <p className="recorded-not-provided" data-testid="bound-scene-missing">
                  无法打开导入时的 Scene Version，未改绑到当前场景
                </p>
              ) : recordedWaitingBound ? (
                <p className="recorded-not-provided" data-testid="bound-scene-pending">
                  正在加载绑定的场景版本…
                </p>
              ) : (
              <>
              <TwinViewerAdapter
                key={`${selectedTask?.id}:${pointCloud.url ?? ''}`}
                sceneMetadata={isRecorded ? boundMetadata! : scene.metadata}
                meshUrl={
                  isRecorded
                    ? (boundMetadata?.mesh_url ?? null)
                    : (scene.metadata.mesh_url ?? null)
                }
                pointCloudUrl={
                  selectedTask?.run_kind === 'recorded'
                    ? (pointCloud.url ?? null)
                    : (scene.metadata.pointcloud_url ?? null)
                }
                displayMode={
                  selectedTask?.run_kind === 'recorded' && !selectedTask.has_pointcloud
                    ? 'mesh'
                    : 'overlay'
                }
                vehiclePose={vehiclePose}
                trajectory={twinTrajectory}
                sensorDevices={selectedTask?.run_kind === 'recorded' ? registeredDevices : scene.devices}
                detectionEvents={displayEvents}
                instrumentLocalizations={viewerLocalizations}
                playbackTime={playbackTime}
                selectedObjectId={selectedLocalization ? localizationObjectId(selectedLocalization.localization.localization_id)
                  : assetSelection?.runId === selectedTask?.id ? assetSelection?.assetId ?? null : null}
                cameraCommand={selectedLocalization ? localizationSelection?.camera ?? null : null}
                onViewerError={error => setViewerError({ binding: viewerBinding, message: error.message })}
                onObjectSelect={event => {
                  if (event.kind === 'localization') {
                    const entry = localizations.rows.find(item => localizationObjectId(item.localization.localization_id) === event.id);
                    if (entry) selectLocalization(entry, false);
                    return;
                  }
                  if (event.kind !== 'device' || !selectedTask || !trajectory[0] || !registeredDevices.some(d => d.id === event.id)) return;
                  setIsPlaying(false);
                  setLocalizationSelection(null);
                  setAssetSelection({ runId: selectedTask.id, assetId: event.id,
                    at: new Date(Date.parse(trajectory[0].timestamp) + playbackTime * 1000).toISOString() });
                }}
              />
              {disclaimer ? (
                <p className="recorded-not-provided" data-testid="display-trajectory-disclaimer">
                  {disclaimer}
                </p>
              ) : null}
              </>
              )}
              {selectedTask?.run_kind === 'recorded' && !selectedTask.has_pointcloud ? (
                <p className="recorded-not-provided" data-testid="pointcloud-not-provided">
                  本包未提供点云
                </p>
              ) : null}
              {isRecorded && pointCloud.loading && <p className="recorded-not-provided">正在校验并读取本运行点云…</p>}
              {isRecorded && pointCloud.notice && <p className="recorded-not-provided">{pointCloud.notice}</p>}
              {isRecorded && pointCloud.error && <p className="recorded-not-provided" role="alert">
                {pointCloud.error} <button type="button" onClick={pointCloud.retry}>重试点云</button>
              </p>}
              {viewerError?.binding === viewerBinding && <p className="recorded-not-provided" role="alert">
                三维场景加载失败，请核对点云格式、场景资产和浏览器图形支持。{viewerError.message}
              </p>}
              {selectedTask?.run_kind === 'recorded' ? (
                <p className="recorded-not-provided" data-testid="devices-not-provided">
                  {assetError || (registeredDevices.length ? `显示 ${registeredDevices.length} 台同版本台账设备；离线表示未连接实时遥测，点击查看当前回放时刻的读数。` : '此场景版本尚无登记设备')}
                </p>
              ) : null}
            </div>
          </div>

          {assetSelection && assetSelection.runId === selectedTask?.id &&
            <section className="panel-card"><div className="panel-header">设备历史证据（选择时刻）
              <button onClick={() => setAssetSelection(null)}>关闭</button></div>
              <RunAssetEvidence key={`${assetSelection.assetId}:${assetSelection.at}`}
                assetId={assetSelection.assetId} at={assetSelection.at} /></section>}

          {selectedTask && <RunStationPanel key={selectedTask.id} runId={selectedTask.id}
            origin={trajectory[0]?.timestamp} end={maxDuration}
            onSeek={time => { setIsPlaying(false); setPlaybackTime(time); }} />}

          {isRecorded && selectedTask && <RunLocalizationPanel runId={selectedTask.id} page={localizations}
            trajectoryStart={trajectory[0]?.timestamp} trajectoryEnd={trajectory[trajectory.length - 1]?.timestamp}
            selected={selectedLocalization} onSelect={selectLocalization} onClose={() => setLocalizationSelection(null)} />}

          <div className="panel-card">
            <div className="panel-header">
              <div className="header-title">
                <Clock className="text-blue" size={18} />
                <span>多通道同步波形（车速 / 震动 / 离层位移）</span>
              </div>
            </div>
            <div className="panel-body chart-body">
              {selectedTask?.run_kind === 'recorded' ? (
                <p className="recorded-not-provided" data-testid="waveform-not-provided">
                  本包未提供多通道波形
                </p>
              ) : (
                <div className="chart-box">
                  <ReactECharts
                    option={getMultiChannelPlaybackOption(playbackTime)}
                    style={{ height: '100%', width: '100%' }}
                  />
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right: Key Event Flags & Log */}
        <div className="panel-card">
          <div className="panel-header">
            <div className="header-title">
              <List className="text-cyan" size={18} />
              <span>关键异常事件</span>
            </div>
            <span className="meta-tag">点击跳转至对应时刻</span>
          </div>
          <div className="panel-body">
            <div className="list-stack">
              {selectedTask?.run_kind === 'recorded' && displayEvents.length === 0 ? (
                <p className="recorded-not-provided" data-testid="events-not-provided">
                  本包未提供异常事件
                </p>
              ) : null}
              {displayEvents.map((evt) => {
                const eventTime = nearestTrajectoryTime(trajectory, evt.position);
                const isCurrent = Math.abs(playbackTime - eventTime) < 5;
                return (
                  <div
                    key={evt.id}
                    className={`timeline-event-item ${isCurrent ? 'active' : ''}`}
                    onClick={() => setPlaybackTime(eventTime)}
                  >
                    <div className="item-time">
                      <MapPin size={14} className="text-cyan" />
                      <span>{formatTime(eventTime)}</span>
                    </div>
                    <div className="item-info">
                      <div className="item-header">
                        <span className="chainage">{evt.id}</span>
                        <span className={`severity-badge ${evt.severity}`}>
                          {severityLabel(evt.severity)}
                        </span>
                      </div>
                      <div className="item-desc">{evt.description ?? evt.type}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
      )}

      {/* Bottom Timeline Controls Dock */}
      {showRecordedEmpty ? null : (
      <div className="playback-dock-bar">
        <div className="dock-left-controls">
          <button
            className="dock-btn play-btn"
            onClick={() => setIsPlaying(!isPlaying)}
          >
            {isPlaying ? <Pause size={18} /> : <Play size={18} />}
            <span>{isPlaying ? '暂停回放' : '开始回放'}</span>
          </button>
          <button
            className="dock-btn"
            onClick={() => setPlaybackTime(0)}
            title="回到起点"
          >
            <RotateCcw size={16} />
          </button>

          <div className="speed-selector">
            <FastForward size={14} className="text-gray" style={{ marginLeft: 8 }} />
            {[1, 2, 4, 8].map((s) => (
              <button
                key={s}
                className={`speed-btn ${speedMultiplier === s ? 'active' : ''}`}
                onClick={() => setSpeedMultiplier(s)}
              >
                {s}x
              </button>
            ))}
          </div>
        </div>

        {/* Timeline Slider with Marker Flags */}
        <div className="dock-center-scrubber">
          <span className="time-text">{formatTime(playbackTime)}</span>
          <div className="scrubber-track-wrapper">
            <input
              type="range"
              min={0}
              max={maxDuration}
              value={playbackTime}
              onChange={(e) => setPlaybackTime(Number(e.target.value))}
              className="scrubber-input"
            />
            {displayEvents.map((evt) => {
              const eventTime = nearestTrajectoryTime(trajectory, evt.position);
              return (
                <div
                  key={evt.id}
                  className={`flag-marker severity-${evt.severity}`}
                  style={{ left: `${maxDuration > 0 ? (eventTime / maxDuration) * 100 : 0}%` }}
                  title={`${evt.id} - ${evt.description ?? evt.type}`}
                  onClick={() => setPlaybackTime(eventTime)}
                />
              );
            })}
          </div>
          <span className="time-text">{formatTime(maxDuration)}</span>
        </div>
      </div>
      )}
    </div>
  );
};
