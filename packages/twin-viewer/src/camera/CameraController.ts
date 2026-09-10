import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { CameraCommand, SceneMetadata, TwinCameraState } from '@digital-twin/contracts';
import { vector3ToPosition } from '../math/matrix.js';
import { sceneBoundsFromMetadata, type SceneBoundsInfo } from '../scene/bounds.js';

export class CameraController {
  readonly controls: OrbitControls | null;
  private followVehicle = false;
  private readonly camera: THREE.PerspectiveCamera;
  private readonly bounds: SceneBoundsInfo;
  private getVehiclePosition: () => THREE.Vector3 | null;
  private findObject: (id: string) => THREE.Object3D | null;
  private disposed = false;

  constructor(
    camera: THREE.PerspectiveCamera,
    dom: HTMLElement,
    meta: SceneMetadata,
    deps: {
      getVehiclePosition: () => THREE.Vector3 | null;
      findObject: (id: string) => THREE.Object3D | null;
      enableControls: boolean;
      fov?: number;
    },
  ) {
    this.camera = camera;
    this.bounds = sceneBoundsFromMetadata(meta);
    this.getVehiclePosition = deps.getVehiclePosition;
    this.findObject = deps.findObject;

    camera.up.set(0, 1, 0);
    camera.fov = deps.fov ?? 50;
    camera.near = this.bounds.near;
    camera.far = this.bounds.far;
    camera.updateProjectionMatrix();

    if (deps.enableControls) {
      this.controls = new OrbitControls(camera, dom);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = 0.08;
      this.controls.screenSpacePanning = true;
      this.controls.minDistance = Math.max(this.bounds.near * 5, 1);
      this.controls.maxDistance = this.bounds.far * 0.45;
      this.controls.maxPolarAngle = Math.PI * 0.92;
      this.controls.addEventListener('change', () => this.clampTarget());
    } else {
      this.controls = null;
    }

    this.reset();
  }

  execute(command: CameraCommand): void {
    switch (command.type) {
      case 'reset':
        this.followVehicle = false;
        this.reset();
        break;
      case 'top':
        this.followVehicle = false;
        this.viewTop();
        break;
      case 'perspective':
        this.followVehicle = false;
        this.viewPerspective();
        break;
      case 'front':
        this.followVehicle = false;
        this.viewFront();
        break;
      case 'followVehicle':
        this.followVehicle = true;
        this.updateFollow();
        break;
      case 'focusObject':
        this.followVehicle = false;
        this.focusObject(command.objectId);
        break;
      default:
        break;
    }
  }

  reset(): void {
    this.viewPerspective();
  }

  viewPerspective(): void {
    const c = this.bounds.center;
    const dist = this.bounds.diagonal * 0.55;
    this.camera.position.set(c.x + dist * 0.45, c.y + dist * 0.35, c.z + dist * 0.55);
    this.lookAt(c.x, c.y, c.z);
  }

  viewTop(): void {
    const c = this.bounds.center;
    const span = Math.max(
      this.bounds.max.x - this.bounds.min.x,
      this.bounds.max.z - this.bounds.min.z,
      20,
    );
    this.camera.position.set(c.x, c.y + span * 1.1, c.z + 0.01);
    this.lookAt(c.x, c.y, c.z);
  }

  viewFront(): void {
    const c = this.bounds.center;
    const depth = Math.max(this.bounds.max.z - this.bounds.min.z, 20);
    this.camera.position.set(c.x, c.y + depth * 0.25, c.z + depth * 1.2);
    this.lookAt(c.x, c.y, c.z);
  }

  focusObject(objectId: string, distance?: number): void {
    const obj = this.findObject(objectId);
    if (!obj) return;
    const box = new THREE.Box3().setFromObject(obj);
    const center = box.getCenter(new THREE.Vector3());
    if (!Number.isFinite(center.x)) obj.getWorldPosition(center);
    const size = box.getSize(new THREE.Vector3()).length();
    const dist = distance ?? Math.max(size * 6, 8);
    const dir = new THREE.Vector3()
      .subVectors(this.camera.position, this.controls?.target ?? center)
      .normalize();
    if (dir.lengthSq() < 1e-6) dir.set(0.5, 0.35, 0.7).normalize();
    this.camera.position.copy(center).addScaledVector(dir, dist);
    this.lookAt(center.x, center.y, center.z);
  }

  fitToScene(root: THREE.Object3D): void {
    const box = new THREE.Box3().setFromObject(root);
    if (box.isEmpty()) {
      this.reset();
      return;
    }
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z, 1);
    const fit = maxDim / (2 * Math.atan((Math.PI * this.camera.fov) / 360));
    const dist = fit * 1.35;
    const dir = new THREE.Vector3(0.45, 0.35, 0.7).normalize();
    this.camera.position.copy(center).addScaledVector(dir, dist);
    this.camera.near = Math.max(this.bounds.near, dist / 200);
    this.camera.far = Math.max(this.bounds.far, dist * 20);
    this.camera.updateProjectionMatrix();
    if (this.controls) {
      this.controls.target.copy(center);
      this.controls.minDistance = Math.max(1, dist * 0.05);
      this.controls.maxDistance = dist * 8;
      this.controls.update();
    } else {
      this.camera.lookAt(center);
    }
  }

  tick(): void {
    if (this.disposed) return;
    if (this.followVehicle) this.updateFollow();
    this.controls?.update();
    this.clampCamera();
  }

  getState(): TwinCameraState {
    const target = this.controls?.target ?? new THREE.Vector3();
    return {
      position: vector3ToPosition(this.camera.position),
      target: vector3ToPosition(target),
    };
  }

  private lookAt(x: number, y: number, z: number): void {
    if (this.controls) {
      this.controls.target.set(x, y, z);
      this.controls.update();
    } else {
      this.camera.lookAt(x, y, z);
    }
  }

  private updateFollow(): void {
    const pos = this.getVehiclePosition();
    if (!pos) return;
    const offset = new THREE.Vector3(-12, 8, 10);
    this.camera.position.copy(pos).add(offset);
    this.lookAt(pos.x, pos.y, pos.z);
  }

  private clampTarget(): void {
    if (!this.controls) return;
    const { min, max } = this.bounds;
    const pad = 20;
    this.controls.target.x = THREE.MathUtils.clamp(this.controls.target.x, min.x - pad, max.x + pad);
    this.controls.target.y = THREE.MathUtils.clamp(this.controls.target.y, min.y - pad, max.y + pad);
    this.controls.target.z = THREE.MathUtils.clamp(this.controls.target.z, min.z - pad, max.z + pad);
  }

  private clampCamera(): void {
    const { min, max } = this.bounds;
    const pad = 80;
    this.camera.position.x = THREE.MathUtils.clamp(this.camera.position.x, min.x - pad, max.x + pad);
    this.camera.position.y = THREE.MathUtils.clamp(
      this.camera.position.y,
      min.y - pad,
      max.y + pad * 2,
    );
    this.camera.position.z = THREE.MathUtils.clamp(this.camera.position.z, min.z - pad, max.z + pad);
  }

  dispose(): void {
    this.disposed = true;
    this.controls?.dispose();
  }
}
