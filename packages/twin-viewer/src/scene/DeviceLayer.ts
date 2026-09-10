import * as THREE from 'three';
import type { SensorDevice } from '@digital-twin/contracts';
import { markSelectable } from '../interaction/selectable.js';
import { positionToVector3 } from '../math/matrix.js';

const STATUS_COLOR: Record<string, number> = {
  online: 0x2ecc71,
  offline: 0x7f8c8d,
};

const TYPE_COLOR: Record<string, number> = {
  delamination: 0x3498db,
  displacement: 0x1abc9c,
  convergence: 0x9b59b6,
  stress: 0xe67e22,
  temperature: 0xe74c3c,
  humidity: 0x5dade2,
  gas: 0xf4d03f,
};

export class DeviceLayer {
  readonly group = new THREE.Group();
  private readonly byId = new Map<string, THREE.Object3D>();
  private devices: SensorDevice[] = [];
  private selectedId: string | null = null;

  constructor() {
    this.group.name = 'device-layer';
  }

  setDevices(devices: readonly SensorDevice[]): void {
    this.clear();
    this.devices = devices.map((d) => ({
      ...d,
      position: d.position ? { ...d.position } : undefined,
    }));
    for (const d of this.devices) {
      if (!d.position) continue;
      const mesh = this.createMarker(d);
      this.byId.set(d.id, mesh);
      this.group.add(mesh);
    }
    this.applySelection();
  }

  setSelectedId(id: string | null): void {
    this.selectedId = id;
    this.applySelection();
  }

  getObject(id: string): THREE.Object3D | undefined {
    return this.byId.get(id);
  }

  getDevice(id: string): SensorDevice | undefined {
    return this.devices.find((d) => d.id === id);
  }

  setVisible(v: boolean): void {
    this.group.visible = v;
  }

  private createMarker(device: SensorDevice): THREE.Mesh {
    const base =
      device.status === 'offline'
        ? STATUS_COLOR.offline!
        : (TYPE_COLOR[device.type] ?? STATUS_COLOR.online!);
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(0.45, 16, 12),
      new THREE.MeshStandardMaterial({
        color: base,
        emissive: base,
        emissiveIntensity: 0.12,
        roughness: 0.45,
      }),
    );
    mesh.name = `device:${device.id}`;
    positionToVector3(device.position!, mesh.position);
    markSelectable(mesh, device.id, 'device');
    return mesh;
  }

  private applySelection(): void {
    for (const [id, obj] of this.byId) {
      const mesh = obj as THREE.Mesh;
      const mat = mesh.material as THREE.MeshStandardMaterial;
      const selected = id === this.selectedId;
      mat.emissiveIntensity = selected ? 0.55 : 0.12;
      mesh.scale.setScalar(selected ? 1.35 : 1);
    }
  }

  private clear(): void {
    for (const obj of this.byId.values()) {
      this.group.remove(obj);
      disposeMesh(obj);
    }
    this.byId.clear();
    this.devices = [];
  }

  dispose(): void {
    this.clear();
  }
}

function disposeMesh(obj: THREE.Object3D): void {
  obj.traverse((c) => {
    if (c instanceof THREE.Mesh) {
      c.geometry.dispose();
      const m = c.material;
      if (Array.isArray(m)) m.forEach((x) => x.dispose());
      else m.dispose();
    }
  });
}
