import * as THREE from 'three';
import type {
  CameraCommand,
  DetectionEvent,
  SceneMetadata,
  SensorDevice,
  TwinCameraState,
  TwinCoordinatePickEvent,
  TwinDisplayMode,
  TwinLoadProgress,
  TwinObjectSelectEvent,
  TwinTrajectoryPoint,
  TwinViewerError,
  VehiclePose,
} from '@digital-twin/contracts';
import type {
  TwinViewerInitProps,
  TwinViewerOptions,
  TwinViewerPublicAPI,
} from './types/viewer.js';
import { loadObjMesh, setMeshOpacity } from './loaders/meshLoader.js';
import { loadPlyPointCloud } from './loaders/pointCloudLoader.js';
import { VehicleLayer } from './scene/VehicleLayer.js';
import { DeviceLayer } from './scene/DeviceLayer.js';
import { EventLayer } from './scene/EventLayer.js';
import { sceneBoundsFromMetadata } from './scene/bounds.js';
import { CameraController } from './camera/CameraController.js';
import { Picker, toObjectSelectEvent, type EnginePickHit } from './interaction/Picker.js';
import { IDENTITY_MATRIX_4 } from './math/matrix.js';

type HandlerMap = {
  objectSelect: Set<(e: TwinObjectSelectEvent) => void>;
  coordinatePick: Set<(e: TwinCoordinatePickEvent) => void>;
  viewerReady: Set<() => void>;
  loadProgress: Set<(e: TwinLoadProgress) => void>;
  viewerError: Set<(e: TwinViewerError) => void>;
  cameraChanged: Set<(e: TwinCameraState) => void>;
};

/**
 * TwinViewer — contracts v2 boundary implementation.
 * Coordinates: scene_local_yup (Y-up meters). Asset transform via options.transformMatrix.
 */
export class TwinViewer implements TwinViewerPublicAPI {
  private readonly container: HTMLElement;
  private readonly meta: SceneMetadata;
  private readonly meshUrl: string | null;
  private readonly pointCloudUrl: string | null;
  private readonly transformMatrix: readonly number[];
  private readonly options: Required<
    Pick<
      TwinViewerOptions,
      | 'maxPixelRatio'
      | 'meshOpacity'
      | 'pointVertexColors'
      | 'pointColor'
      | 'trajectoryPastColor'
      | 'trajectoryFutureColor'
      | 'enableControls'
      | 'fov'
    >
  > & { background: number; pointSize?: number };

  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly meshGroup = new THREE.Group();
  private readonly pointsGroup = new THREE.Group();
  private readonly entityGroup = new THREE.Group();

  private readonly vehicleLayer: VehicleLayer;
  private readonly deviceLayer: DeviceLayer;
  private readonly eventLayer: EventLayer;
  private cameraController: CameraController | null = null;
  private picker: Picker | null = null;

  private displayMode: TwinDisplayMode = 'overlay';
  private playbackTime = 0;
  private selectedObjectId: string | null = null;
  private meshObject: THREE.Object3D | null = null;
  private pointsObject: THREE.Points | null = null;

  private meshLoaded = false;
  private pointsLoaded = false;
  private loadPromise: Promise<void> | null = null;
  private readyEmitted = false;
  private disposed = false;
  private raf = 0;
  private lastCameraEmit = 0;
  private resizeObserver: ResizeObserver | null = null;
  private canvasStatus = '初始化中';

  private readonly handlers: HandlerMap = {
    objectSelect: new Set(),
    coordinatePick: new Set(),
    viewerReady: new Set(),
    loadProgress: new Set(),
    viewerError: new Set(),
    cameraChanged: new Set(),
  };

  constructor(container: HTMLElement, props: TwinViewerInitProps) {
    if (!container) throw new Error('TwinViewer requires a container HTMLElement');
    this.container = container;
    this.meta = props.sceneMetadata;
    this.meshUrl = props.meshUrl;
    this.pointCloudUrl = props.pointCloudUrl;
    this.displayMode = props.displayMode ?? 'overlay';

    const opt = props.options ?? {};
    this.transformMatrix =
      opt.transformMatrix && opt.transformMatrix.length === 16
        ? opt.transformMatrix
        : IDENTITY_MATRIX_4;
    this.options = {
      background: parseColor(opt.background ?? 0x12151a),
      maxPixelRatio: opt.maxPixelRatio ?? 2,
      meshOpacity: opt.meshOpacity ?? 0.92,
      pointSize: opt.pointSize,
      pointVertexColors: opt.pointVertexColors ?? true,
      pointColor: opt.pointColor ?? 0xb8c4d0,
      trajectoryPastColor: opt.trajectoryPastColor ?? 0xf5a623,
      trajectoryFutureColor: opt.trajectoryFutureColor ?? 0x5dade2,
      enableControls: opt.enableControls ?? true,
      fov: opt.fov ?? 50,
    };

    this.scene.background = new THREE.Color(this.options.background);
    this.scene.up.set(0, 1, 0);

    const bounds = sceneBoundsFromMetadata(this.meta);
    const w = Math.max(container.clientWidth, 1);
    const h = Math.max(container.clientHeight, 1);
    this.camera = new THREE.PerspectiveCamera(
      this.options.fov,
      w / h,
      bounds.near,
      bounds.far,
    );
    this.camera.up.set(0, 1, 0);

    this.renderer = createWebGLRenderer();
    this.renderer.setPixelRatio(
      Math.min(
        typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1,
        this.options.maxPixelRatio,
      ),
    );
    this.renderer.setSize(w, h, false);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.domElement.style.display = 'block';
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    this.renderer.domElement.style.touchAction = 'none';
    this.renderer.domElement.setAttribute('role', 'img');
    this.updateCanvasStatus('初始化中');
    this.renderer.domElement.addEventListener('webglcontextlost', this.handleContextLost);
    container.appendChild(this.renderer.domElement);

    this.addLights();
    this.meshGroup.name = 'mesh-root';
    this.pointsGroup.name = 'points-root';
    this.entityGroup.name = 'entity-root';
    this.scene.add(this.meshGroup);
    this.scene.add(this.pointsGroup);
    this.scene.add(this.entityGroup);

    this.vehicleLayer = new VehicleLayer(
      this.options.trajectoryPastColor,
      this.options.trajectoryFutureColor,
    );
    this.deviceLayer = new DeviceLayer();
    this.eventLayer = new EventLayer();
    this.entityGroup.add(this.vehicleLayer.group);
    this.entityGroup.add(this.deviceLayer.group);
    this.entityGroup.add(this.eventLayer.group);

    this.cameraController = new CameraController(
      this.camera,
      this.renderer.domElement,
      this.meta,
      {
        enableControls: this.options.enableControls,
        fov: this.options.fov,
        getVehiclePosition: () =>
          this.vehicleLayer.getVehicleObject().visible
            ? this.vehicleLayer.getPosition()
            : null,
        findObject: (id) => this.findObject(id),
      },
    );

    this.picker = new Picker(this.camera, this.renderer.domElement, {
      mesh: this.meshGroup,
      points: this.pointsGroup,
      entities: this.entityGroup,
    });
    this.picker.onPick = (result) => this.handlePick(result);
    this.picker.onFocusRequest = (id) =>
      this.cameraController?.execute({ type: 'focusObject', objectId: id });

    this.applyDisplayMode();

    if (typeof ResizeObserver !== 'undefined') {
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(container);
    }

    this.loop();
  }

  onObjectSelect(handler: (e: TwinObjectSelectEvent) => void): () => void {
    this.handlers.objectSelect.add(handler);
    return () => this.handlers.objectSelect.delete(handler);
  }
  onCoordinatePick(handler: (e: TwinCoordinatePickEvent) => void): () => void {
    this.handlers.coordinatePick.add(handler);
    return () => this.handlers.coordinatePick.delete(handler);
  }
  onViewerReady(handler: () => void): () => void {
    this.handlers.viewerReady.add(handler);
    if (this.readyEmitted) handler();
    return () => this.handlers.viewerReady.delete(handler);
  }
  onLoadProgress(handler: (e: TwinLoadProgress) => void): () => void {
    this.handlers.loadProgress.add(handler);
    return () => this.handlers.loadProgress.delete(handler);
  }
  onViewerError(handler: (e: TwinViewerError) => void): () => void {
    this.handlers.viewerError.add(handler);
    return () => this.handlers.viewerError.delete(handler);
  }
  onCameraChanged(handler: (e: TwinCameraState) => void): () => void {
    this.handlers.cameraChanged.add(handler);
    return () => this.handlers.cameraChanged.delete(handler);
  }

  async load(): Promise<void> {
    this.assertAlive();
    if (this.meshLoaded && this.pointsLoaded) return;
    if (this.loadPromise) return this.loadPromise;

    this.loadPromise = this.doLoad().finally(() => {
      this.loadPromise = null;
    });
    return this.loadPromise;
  }

  private async doLoad(): Promise<void> {
    const bounds = sceneBoundsFromMetadata(this.meta);
    const autoPointSize =
      this.options.pointSize ?? Math.max(0.08, bounds.diagonal / 2500);

    try {
      if (!this.meshLoaded) {
        if (!this.meshUrl) {
          this.meshLoaded = true;
          this.emitProgress('idle', 0.45);
        } else {
          this.emitProgress('mesh', 0);
          const mesh = await loadObjMesh(this.meshUrl, {
            transformMatrix: this.transformMatrix,
            opacity: this.options.meshOpacity,
            onProgress: (loaded, total) =>
              this.emitProgress('mesh', 0.45 * (total ? loaded / total : 0)),
          });
          this.clearGroup(this.meshGroup);
          this.meshGroup.add(mesh);
          this.meshObject = mesh;
          this.meshLoaded = true;
          this.emitProgress('mesh', 0.45);
        }
      }

      if (!this.pointsLoaded) {
        if (!this.pointCloudUrl) {
          this.pointsLoaded = true;
          this.emitProgress('idle', 0.95);
        } else {
          this.emitProgress('pointcloud', 0.45);
          const pts = await loadPlyPointCloud(this.pointCloudUrl, {
            transformMatrix: this.transformMatrix,
            pointSize: autoPointSize,
            vertexColors: this.options.pointVertexColors,
            fallbackColor: this.options.pointColor,
            onProgress: (loaded, total) =>
              this.emitProgress('pointcloud', 0.45 + 0.5 * (total ? loaded / total : 0)),
          });
          this.clearGroup(this.pointsGroup);
          this.pointsGroup.add(pts);
          this.pointsObject = pts;
          this.pointsLoaded = true;
          this.picker?.setPointThreshold(Math.max(0.25, autoPointSize * 3));
          this.emitProgress('pointcloud', 0.95);
        }
      }

      this.applyDisplayMode();
      // Asset parsing can finish before Safari has delivered its first ResizeObserver
      // notification. Reconcile the drawing buffer with the final layout before fitting.
      this.resize();
      this.cameraController?.fitToScene(this.scene);
      this.emitProgress('idle', 1);
      if (!this.readyEmitted) {
        this.readyEmitted = true;
        for (const h of this.handlers.viewerReady) h();
      }
    } catch (err) {
      this.emitError({
        code: 'LOAD_FAILED',
        message: err instanceof Error ? err.message : String(err),
      });
      throw err;
    }
  }

  setDisplayMode(mode: TwinDisplayMode): void {
    this.assertAlive();
    this.displayMode = mode;
    this.applyDisplayMode();
  }

  getDisplayMode(): TwinDisplayMode {
    return this.displayMode;
  }

  setVehiclePose(pose: VehiclePose | null): void {
    this.assertAlive();
    this.vehicleLayer.setVehiclePose(pose);
  }

  setTrajectory(points: TwinTrajectoryPoint[]): void {
    this.assertAlive();
    this.vehicleLayer.setTrajectory(points);
  }

  setSensorDevices(devices: SensorDevice[]): void {
    this.assertAlive();
    this.deviceLayer.setDevices(devices);
    this.deviceLayer.setSelectedId(this.selectedObjectId);
  }

  setDetectionEvents(events: DetectionEvent[]): void {
    this.assertAlive();
    this.eventLayer.setEvents(events);
    this.eventLayer.setSelectedId(this.selectedObjectId);
  }

  setPlaybackTime(time: number): void {
    this.assertAlive();
    this.playbackTime = time;
    this.vehicleLayer.setPlaybackTime(time);
  }

  getPlaybackTime(): number {
    return this.playbackTime;
  }

  setSelectedObjectId(id: string | null): void {
    this.assertAlive();
    this.selectedObjectId = id;
    this.deviceLayer.setSelectedId(id);
    this.eventLayer.setSelectedId(id);
  }

  executeCameraCommand(command: CameraCommand): void {
    this.assertAlive();
    this.cameraController?.execute(command);
  }

  setMeshOpacity(opacity: number): void {
    this.assertAlive();
    this.options.meshOpacity = opacity;
    if (this.meshObject) setMeshOpacity(this.meshObject, opacity);
  }

  fitToScene(): void {
    this.assertAlive();
    this.cameraController?.fitToScene(this.scene);
  }

  getCameraState(): TwinCameraState {
    return (
      this.cameraController?.getState() ?? {
        position: { x: 0, y: 0, z: 0 },
        target: { x: 0, y: 0, z: 0 },
      }
    );
  }

  /** Test helper */
  pickAtNdc(ndcX: number, ndcY: number): EnginePickHit | null {
    return this.picker?.pickNdc(ndcX, ndcY) ?? null;
  }

  resize(): void {
    if (this.disposed) return;
    const w = Math.max(this.container.clientWidth, 1);
    const h = Math.max(this.container.clientHeight, 1);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    this.updateCanvasStatus(this.canvasStatus);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    this.resizeObserver?.disconnect();
    this.renderer.domElement.removeEventListener('webglcontextlost', this.handleContextLost);
    this.picker?.dispose();
    this.cameraController?.dispose();
    this.vehicleLayer.dispose();
    this.deviceLayer.dispose();
    this.eventLayer.dispose();
    this.clearGroup(this.meshGroup);
    this.clearGroup(this.pointsGroup);
    this.renderer.dispose();
    // Safari/WebKit may retain detached canvas contexts until GC. Explicitly return the
    // context when an SPA route unmounts so repeated page switches do not degrade to black.
    this.renderer.forceContextLoss();
    if (this.renderer.domElement.parentElement === this.container) {
      this.container.removeChild(this.renderer.domElement);
    }
    for (const set of Object.values(this.handlers)) set.clear();
  }

  private applyDisplayMode(): void {
    const mode = this.displayMode;
    this.meshGroup.visible = mode === 'mesh' || mode === 'overlay';
    this.pointsGroup.visible = mode === 'pointcloud' || mode === 'overlay';
    this.entityGroup.visible = true;
  }

  private findObject(id: string): THREE.Object3D | null {
    if (id === 'inspection-vehicle' || id === 'vehicle') {
      return this.vehicleLayer.getVehicleObject();
    }
    return this.deviceLayer.getObject(id) ?? this.eventLayer.getObject(id) ?? null;
  }

  private handlePick(result: EnginePickHit): void {
    // Contracts: coordinate pick is position-only
    this.handlers.coordinatePick.forEach((h) => h({ position: result.position }));

    const select = toObjectSelectEvent(result);
    if (select) {
      this.handlers.objectSelect.forEach((h) => h(select));
      this.setSelectedObjectId(select.id);
    }
  }

  private emitProgress(stage: TwinLoadProgress['stage'], ratio: number): void {
    const ev: TwinLoadProgress = { stage, ratio };
    if (stage === 'idle' && ratio >= 1) {
      this.updateCanvasStatus('已就绪');
    } else {
      const stageLabel =
        stage === 'mesh'
          ? '模型'
          : stage === 'pointcloud'
            ? '点云'
            : stage === 'vehicle'
              ? '车辆'
              : '场景';
      this.updateCanvasStatus(`正在加载${stageLabel} ${Math.round(ratio * 100)}%`);
    }
    this.handlers.loadProgress.forEach((h) => h(ev));
  }

  private emitError(ev: TwinViewerError): void {
    this.updateCanvasStatus('加载失败');
    this.handlers.viewerError.forEach((h) => h(ev));
    this.showErrorOverlay(ev.message);
  }

  private updateCanvasStatus(status: string): void {
    this.canvasStatus = status;
    const canvas = this.renderer.domElement;
    const profile =
      canvas.dataset.webglProfile === 'compatibility' ? '，兼容模式' : '';
    canvas.setAttribute(
      'aria-label',
      `三维场景${status}（${canvas.width}×${canvas.height}${profile}）`,
    );
  }

  private handleContextLost = (event: Event): void => {
    if (this.disposed) return;
    // Allow WebKit/Three.js to restore the context while ensuring the UI never fails silently.
    event.preventDefault();
    this.emitError({
      code: 'WEBGL_CONTEXT_LOST',
      message: '浏览器三维渲染上下文已丢失，请刷新页面后重试',
    });
  };

  private showErrorOverlay(message: string): void {
    let el = this.container.querySelector('.twin-viewer-error') as HTMLDivElement | null;
    if (!el) {
      el = document.createElement('div');
      el.className = 'twin-viewer-error';
      el.style.cssText =
        'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(10,12,16,.88);color:#f5f6f7;font:14px/1.5 system-ui;padding:24px;text-align:center;z-index:5;';
      if (getComputedStyle(this.container).position === 'static') {
        this.container.style.position = 'relative';
      }
      this.container.appendChild(el);
    }
    el.textContent = `TwinViewer 加载失败：${message}`;
  }

  private addLights(): void {
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const key = new THREE.DirectionalLight(0xffffff, 0.85);
    key.position.set(40, 80, 30);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xa0b4c8, 0.35);
    fill.position.set(-50, 30, -40);
    this.scene.add(fill);
  }

  private loop = (): void => {
    if (this.disposed) return;
    this.raf = requestAnimationFrame(this.loop);
    this.cameraController?.tick();
    this.renderer.render(this.scene, this.camera);

    const now = performance.now();
    if (now - this.lastCameraEmit > 200) {
      this.lastCameraEmit = now;
      const state = this.getCameraState();
      this.handlers.cameraChanged.forEach((h) => h(state));
    }
  };

  private clearGroup(group: THREE.Group): void {
    while (group.children.length > 0) {
      const child = group.children[0]!;
      group.remove(child);
      child.traverse((obj) => {
        if (
          obj instanceof THREE.Mesh ||
          obj instanceof THREE.Points ||
          obj instanceof THREE.Line
        ) {
          const m = obj as THREE.Mesh;
          m.geometry?.dispose();
          const mat = m.material;
          if (Array.isArray(mat)) mat.forEach((x) => x.dispose());
          else mat?.dispose();
        }
      });
    }
  }

  private assertAlive(): void {
    if (this.disposed) throw new Error('TwinViewer has been disposed');
  }
}

function parseColor(c: number | string): number {
  if (typeof c === 'number') return c;
  return new THREE.Color(c).getHex();
}

function createWebGLRenderer(): THREE.WebGLRenderer {
  const highPerformance = {
    options: { antialias: true, powerPreference: 'high-performance' } as const,
    profile: 'high-performance',
  };
  const compatibility = {
    options: { antialias: false, powerPreference: 'default' } as const,
    profile: 'compatibility',
  };
  const safari = isSafariBrowser();
  const primary = safari ? compatibility : highPerformance;
  const fallback = safari ? highPerformance : compatibility;

  try {
    return createWebGLRendererForProfile(primary);
  } catch (primaryError) {
    try {
      return createWebGLRendererForProfile(fallback);
    } catch (fallbackError) {
      const primaryMessage =
        primaryError instanceof Error ? primaryError.message : String(primaryError);
      const fallbackMessage =
        fallbackError instanceof Error ? fallbackError.message : String(fallbackError);
      throw new Error(
        `WebGL renderer initialization failed (high-performance: ${primaryMessage}; fallback: ${fallbackMessage})`,
      );
    }
  }
}

function createWebGLRendererForProfile(config: {
  options: THREE.WebGLRendererParameters;
  profile: string;
}): THREE.WebGLRenderer {
  const renderer = new THREE.WebGLRenderer(config.options);
  renderer.domElement.dataset.webglProfile = config.profile;
  return renderer;
}

function isSafariBrowser(): boolean {
  if (typeof navigator === 'undefined') return false;
  const userAgent = navigator.userAgent;
  return (
    /Safari\//.test(userAgent) &&
    !/(?:Chrome|Chromium|CriOS|Edg|EdgiOS|FxiOS|OPR)\//.test(userAgent)
  );
}
