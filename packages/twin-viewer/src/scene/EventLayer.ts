import * as THREE from 'three';
import type { DetectionEvent } from '@digital-twin/contracts';
import { markSelectable } from '../interaction/selectable.js';
import { positionToVector3 } from '../math/matrix.js';

const ORANGE = 0xe67e22;

export class EventLayer {
  readonly group = new THREE.Group();
  private readonly byId = new Map<string, THREE.Object3D>();
  private events: DetectionEvent[] = [];
  private selectedId: string | null = null;

  constructor() {
    this.group.name = 'event-layer';
  }

  setEvents(events: readonly DetectionEvent[]): void {
    this.clear();
    this.events = events.map((e) => ({
      ...e,
      position: { ...e.position },
    }));
    for (const e of this.events) {
      const obj = this.createMarker(e);
      this.byId.set(e.id, obj);
      this.group.add(obj);
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

  getEvent(id: string): DetectionEvent | undefined {
    return this.events.find((e) => e.id === id);
  }

  setVisible(v: boolean): void {
    this.group.visible = v;
  }

  private createMarker(event: DetectionEvent): THREE.Object3D {
    const size = severitySize(event.severity);
    const mesh = new THREE.Mesh(
      new THREE.OctahedronGeometry(size, 0),
      new THREE.MeshStandardMaterial({
        color: ORANGE,
        emissive: ORANGE,
        emissiveIntensity: 0.2,
        transparent: true,
        opacity: 0.85,
        roughness: 0.4,
      }),
    );
    mesh.name = `event:${event.id}`;
    positionToVector3(event.position, mesh.position);
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry),
      new THREE.LineBasicMaterial({ color: 0xd35400 }),
    );
    mesh.add(edges);
    markSelectable(mesh, event.id, 'event');
    return mesh;
  }

  private applySelection(): void {
    for (const [id, obj] of this.byId) {
      const mesh = obj as THREE.Mesh;
      const mat = mesh.material as THREE.MeshStandardMaterial;
      const selected = id === this.selectedId;
      mat.emissiveIntensity = selected ? 0.7 : 0.2;
      mesh.scale.setScalar(selected ? 1.4 : 1);
    }
  }

  private clear(): void {
    for (const obj of this.byId.values()) {
      this.group.remove(obj);
      disposeObject(obj);
    }
    this.byId.clear();
    this.events = [];
  }

  dispose(): void {
    this.clear();
  }
}

function severitySize(s: DetectionEvent['severity']): number {
  switch (s) {
    case 'critical':
      return 0.9;
    case 'high':
      return 0.75;
    case 'medium':
      return 0.6;
    default:
      return 0.5;
  }
}

function disposeObject(obj: THREE.Object3D): void {
  obj.traverse((c) => {
    if (c instanceof THREE.Mesh || c instanceof THREE.LineSegments) {
      const m = c as THREE.Mesh;
      m.geometry.dispose();
      const mat = m.material;
      if (Array.isArray(mat)) mat.forEach((x) => x.dispose());
      else mat.dispose();
    }
  });
}
