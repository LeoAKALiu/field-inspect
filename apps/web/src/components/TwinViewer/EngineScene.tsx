import React, { useEffect, useMemo, useRef, useState } from 'react';
import { TwinViewerView } from '@digital-twin/twin-viewer/react';
import { IDENTITY_MATRIX_4, type TwinViewerPublicAPI } from '@digital-twin/twin-viewer';
import type { TwinDisplayMode, TwinViewerProps } from '@digital-twin/contracts';
import { LIRIS_MESH_URL, LIRIS_POINTCLOUD_URL, useEngineSceneMetadata } from '../../services/scene/engineMetadata';
import { MockViewer } from './MockViewer';
import { Box, Cloud, Crosshair, Layers, RotateCcw } from 'lucide-react';

/* ============================================================================
 * 类型边界（契约 v2）
 *
 * twin-viewer 引擎公共 API 已对齐契约 v2（packages/twin-viewer 直接 re-export
 * @digital-twin/contracts 类型，坐标系统一 scene_local_yup，Y 轴向上，米），
 * 因此本组件不再做任何字段/坐标映射，业务层 props 原样透传至 TwinViewerView。
 * ========================================================================== */

const DISPLAY_MODE_OPTIONS: Array<{ mode: TwinDisplayMode; label: string; icon: React.ReactNode }> = [
  { mode: 'mesh', label: '实体网格', icon: <Box size={16} /> },
  { mode: 'pointcloud', label: '点云云图', icon: <Cloud size={16} /> },
  { mode: 'overlay', label: '融合叠加', icon: <Layers size={16} /> },
];

/**
 * 引擎场景（经动态 import 懒加载；加载/运行失败由 TwinViewerAdapter 降级 MockViewer）。
 * 实现契约 v2 TwinViewerProps，内部包装 @digital-twin/twin-viewer 的 TwinViewerView。
 */
export default function EngineScene(props: TwinViewerProps) {
  const {
    sceneMetadata,
    meshUrl,
    pointCloudUrl,
    displayMode,
    vehiclePose,
    trajectory,
    sensorDevices,
    detectionEvents,
    instrumentLocalizations,
    playbackTime,
    selectedObjectId,
    cameraCommand,
    height = '100%',
    onObjectSelect,
    onCoordinatePick,
    onViewerReady,
    onLoadProgress,
    onViewerError,
    onCameraChanged,
  } = props;

  const lirisMesh = meshUrl === LIRIS_MESH_URL;
  const lirisCloud = pointCloudUrl === LIRIS_POINTCLOUD_URL;
  const usesLirisAssets = lirisMesh || lirisCloud;
  const { data: engineMetadata, loading: metaLoading, error: metaError } = useEngineSceneMetadata(usesLirisAssets);
  const viewerApiRef = useRef<TwinViewerPublicAPI>(null);

  // HUD：显示模式（与 props.displayMode 双向同步，页面与 HUD 均可切换）
  const [mode, setMode] = useState<TwinDisplayMode>(displayMode);
  useEffect(() => setMode(displayMode), [displayMode]);

  // HUD：图层开关（引擎无分层可见性 API，通过过滤数据数组实现）
  const [showDevices, setShowDevices] = useState(true);
  const [showEvents, setShowEvents] = useState(true);
  const [showTrajectory, setShowTrajectory] = useState(true);
  const [showLocalizations, setShowLocalizations] = useState(true);

  const engineDevices = useMemo(
    () => (showDevices ? sensorDevices : []),
    [sensorDevices, showDevices],
  );
  const engineEvents = useMemo(
    () => (showEvents ? detectionEvents : []),
    [detectionEvents, showEvents],
  );

  if (usesLirisAssets && metaError) {
    return <MockViewer state="unavailable" height={height} />;
  }
  if (usesLirisAssets && (metaLoading || !engineMetadata)) {
    return <MockViewer state="loading" height={height} />;
  }

  return (
    <div className="twin-adapter" style={{ height }}>
      <TwinViewerView
        ref={viewerApiRef}
        className="twin-canvas"
        sceneMetadata={sceneMetadata}
        meshUrl={meshUrl ?? null}
        pointCloudUrl={pointCloudUrl ?? null}
        displayMode={mode}
        options={{
          transformMatrix: lirisMesh ? engineMetadata?.transformMatrix : undefined,
          pointCloudTransformMatrix: lirisCloud ? engineMetadata?.transformMatrix : IDENTITY_MATRIX_4,
        }}
        vehiclePose={vehiclePose}
        trajectory={showTrajectory ? trajectory : []}
        sensorDevices={engineDevices}
        detectionEvents={engineEvents}
        instrumentLocalizations={showLocalizations ? instrumentLocalizations : []}
        playbackTime={playbackTime}
        selectedObjectId={selectedObjectId}
        cameraCommand={cameraCommand}
        onObjectSelect={onObjectSelect}
        onCoordinatePick={onCoordinatePick}
        onViewerReady={() => {
          // 引擎完成资产 fitToScene 后会重置相机；此处重放页面的初始定位命令，
          // 确保巡检页首屏真正聚焦车辆，而不是被加载收尾覆盖。
          if (cameraCommand) viewerApiRef.current?.executeCameraCommand(cameraCommand);
          onViewerReady?.();
        }}
        onLoadProgress={onLoadProgress}
        onViewerError={onViewerError}
        onCameraChanged={onCameraChanged}
      />

      {/* HUD 覆盖控件 */}
      <div className="viewer-hud-overlay">
        <div className="hud-row">
          <div className="hud-group">
            {DISPLAY_MODE_OPTIONS.map((opt) => (
              <button
                key={opt.mode}
                className={`hud-btn ${mode === opt.mode ? 'active' : ''}`}
                onClick={() => setMode(opt.mode)}
              >
                {opt.icon}
                <span>{opt.label}</span>
              </button>
            ))}
          </div>

          <div className="hud-panel">
            <label className="hud-checkbox">
              <input
                type="checkbox"
                checked={showDevices}
                onChange={(e) => setShowDevices(e.target.checked)}
              />
              <span>监测设备 ({sensorDevices.length})</span>
            </label>
            <label className="hud-checkbox">
              <input
                type="checkbox"
                checked={showEvents}
                onChange={(e) => setShowEvents(e.target.checked)}
              />
              <span>异常候选 ({detectionEvents.length})</span>
            </label>
            <label className="hud-checkbox">
              <input type="checkbox" checked={showLocalizations} onChange={event => setShowLocalizations(event.target.checked)} />
              <span>观测定位 · 黄色方框 ({instrumentLocalizations?.filter(record => record.position).length ?? 0})</span>
            </label>
            <label className="hud-checkbox">
              <input
                type="checkbox"
                checked={showTrajectory}
                onChange={(e) => setShowTrajectory(e.target.checked)}
              />
              <span>巡检轨迹</span>
            </label>
          </div>
        </div>

        <div className="hud-row hud-bottom-row">
          <span className="scene-corner-badge">
            {sceneMetadata.scene_version_id
              ? `场景版本 ${sceneMetadata.scene_version_id}｜运行轨迹与证据按各自来源展示`
              : 'LIRIS 公开 OBJ/PLY｜演示路线与模拟车辆/监测'}
          </span>
          <div className="hud-actions-group">
            <button
              className="hud-action-btn"
              onClick={() =>
                viewerApiRef.current?.executeCameraCommand({
                  type: 'focusObject',
                  objectId: 'inspection-vehicle',
                })
              }
              title="定位巡检车"
            >
              <Crosshair size={16} />
              <span>定位巡检车</span>
            </button>
            <button
              className="hud-action-btn"
              onClick={() => viewerApiRef.current?.executeCameraCommand({ type: 'reset' })}
              title="重置视角"
            >
              <RotateCcw size={16} />
              <span>视角复位</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
