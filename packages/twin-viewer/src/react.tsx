import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  type CSSProperties,
} from 'react';
import { TwinViewer } from './TwinViewer.js';
import type {
  TwinViewerOptions,
  TwinViewerPublicAPI,
} from './types/viewer.js';
import type {
  CameraCommand,
  DetectionEvent,
  SensorDevice,
  TwinCameraState,
  TwinCoordinatePickEvent,
  TwinDisplayMode,
  TwinLoadProgress,
  TwinObjectSelectEvent,
  TwinTrajectoryPoint,
  TwinViewerError,
  TwinViewerProps,
  VehiclePose,
} from '@digital-twin/contracts';

export type TwinViewerViewProps = TwinViewerProps & {
  meshOpacity?: number;
  options?: TwinViewerOptions;
  className?: string;
  style?: CSSProperties;
};

/**
 * React binding aligned to contracts `TwinViewerProps`.
 * Imperative API via ref; props drive data and camera commands.
 */
export const TwinViewerView = forwardRef<TwinViewerPublicAPI, TwinViewerViewProps>(
  function TwinViewerView(props, ref) {
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerRef = useRef<TwinViewer | null>(null);
    const lastCameraCmd = useRef<CameraCommand | null | undefined>(undefined);

    useImperativeHandle(ref, () => createFacade(() => viewerRef.current), []);

    useEffect(() => {
      const el = containerRef.current;
      if (!el) return;

      let viewer: TwinViewer;
      try {
        viewer = new TwinViewer(el, {
          sceneMetadata: props.sceneMetadata,
          meshUrl: props.meshUrl,
          pointCloudUrl: props.pointCloudUrl,
          displayMode: props.displayMode,
          options: {
            ...props.options,
            meshOpacity: props.meshOpacity ?? props.options?.meshOpacity,
          },
        });
      } catch (error) {
        props.onViewerError?.({
          code: 'INITIALIZATION_FAILED',
          message: error instanceof Error ? error.message : String(error),
        });
        return;
      }
      viewerRef.current = viewer;

      const offs = [
        props.onObjectSelect ? viewer.onObjectSelect(props.onObjectSelect) : () => {},
        props.onCoordinatePick ? viewer.onCoordinatePick(props.onCoordinatePick) : () => {},
        props.onViewerReady ? viewer.onViewerReady(props.onViewerReady) : () => {},
        props.onLoadProgress ? viewer.onLoadProgress(props.onLoadProgress) : () => {},
        props.onViewerError ? viewer.onViewerError(props.onViewerError) : () => {},
        props.onCameraChanged ? viewer.onCameraChanged(props.onCameraChanged) : () => {},
      ];

      if (props.trajectory?.length) viewer.setTrajectory(props.trajectory);
      if (props.sensorDevices?.length) viewer.setSensorDevices(props.sensorDevices);
      if (props.detectionEvents?.length) viewer.setDetectionEvents(props.detectionEvents);
      viewer.setVehiclePose(props.vehiclePose ?? null);
      viewer.setPlaybackTime(props.playbackTime ?? 0);
      viewer.setSelectedObjectId(props.selectedObjectId ?? null);

      void viewer.load().catch(() => {
        /* errors via onViewerError */
      });

      return () => {
        offs.forEach((off) => off());
        viewer.dispose();
        viewerRef.current = null;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [props.sceneMetadata.scene_id, props.meshUrl, props.pointCloudUrl]);

    useEffect(() => {
      viewerRef.current?.setDisplayMode(props.displayMode);
    }, [props.displayMode]);

    useEffect(() => {
      viewerRef.current?.setVehiclePose(props.vehiclePose ?? null);
    }, [props.vehiclePose]);

    useEffect(() => {
      viewerRef.current?.setTrajectory(props.trajectory ?? []);
    }, [props.trajectory]);

    useEffect(() => {
      viewerRef.current?.setSensorDevices(props.sensorDevices ?? []);
    }, [props.sensorDevices]);

    useEffect(() => {
      viewerRef.current?.setDetectionEvents(props.detectionEvents ?? []);
    }, [props.detectionEvents]);

    useEffect(() => {
      viewerRef.current?.setPlaybackTime(props.playbackTime ?? 0);
    }, [props.playbackTime]);

    useEffect(() => {
      viewerRef.current?.setSelectedObjectId(props.selectedObjectId ?? null);
    }, [props.selectedObjectId]);

    useEffect(() => {
      if (props.meshOpacity !== undefined) {
        viewerRef.current?.setMeshOpacity(props.meshOpacity);
      }
    }, [props.meshOpacity]);

    useEffect(() => {
      const cmd = props.cameraCommand;
      if (!cmd) return;
      if (cmd === lastCameraCmd.current) return;
      lastCameraCmd.current = cmd;
      viewerRef.current?.executeCameraCommand(cmd);
    }, [props.cameraCommand]);

    useEffect(() => {
      const v = viewerRef.current;
      if (!v || !props.onObjectSelect) return;
      return v.onObjectSelect(props.onObjectSelect);
    }, [props.onObjectSelect]);

    useEffect(() => {
      const v = viewerRef.current;
      if (!v || !props.onCoordinatePick) return;
      return v.onCoordinatePick(props.onCoordinatePick);
    }, [props.onCoordinatePick]);

    const height = props.height ?? '100%';

    return (
      <div
        ref={containerRef}
        className={props.className}
        style={{
          width: '100%',
          height: typeof height === 'number' ? `${height}px` : height,
          position: 'relative',
          ...props.style,
        }}
        data-testid="twin-viewer-root"
      />
    );
  },
);

function createFacade(get: () => TwinViewer | null): TwinViewerPublicAPI {
  const need = () => {
    const v = get();
    if (!v) throw new Error('TwinViewer is not mounted');
    return v;
  };
  return {
    setDisplayMode: (m: TwinDisplayMode) => need().setDisplayMode(m),
    getDisplayMode: () => need().getDisplayMode(),
    setVehiclePose: (p: VehiclePose | null) => need().setVehiclePose(p),
    setTrajectory: (t: TwinTrajectoryPoint[]) => need().setTrajectory(t),
    setSensorDevices: (d: SensorDevice[]) => need().setSensorDevices(d),
    setDetectionEvents: (e: DetectionEvent[]) => need().setDetectionEvents(e),
    setPlaybackTime: (t: number) => need().setPlaybackTime(t),
    getPlaybackTime: () => need().getPlaybackTime(),
    setSelectedObjectId: (id: string | null) => need().setSelectedObjectId(id),
    executeCameraCommand: (c: CameraCommand) => need().executeCameraCommand(c),
    setMeshOpacity: (o: number) => need().setMeshOpacity(o),
    fitToScene: () => need().fitToScene(),
    resize: () => need().resize(),
    dispose: () => need().dispose(),
    getCameraState: (): TwinCameraState => need().getCameraState(),
    load: () => need().load(),
  };
}

export type {
  TwinViewerProps,
  TwinObjectSelectEvent,
  TwinCoordinatePickEvent,
  TwinLoadProgress,
  TwinViewerError,
  TwinCameraState,
  TwinDisplayMode,
  VehiclePose,
  TwinTrajectoryPoint,
  CameraCommand,
};
