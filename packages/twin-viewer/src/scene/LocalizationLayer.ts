import * as THREE from 'three';
import { localizationObjectId, type InstrumentLocalization } from '@digital-twin/contracts';
import { markSelectable } from '../interaction/selectable.js';
import { positionToVector3 } from '../math/matrix.js';

/** Observation estimates stay visually and semantically separate from registered devices. */
export class LocalizationLayer {
  readonly group = new THREE.Group();
  private readonly byId = new Map<string, THREE.Mesh<THREE.BoxGeometry, THREE.MeshBasicMaterial>>();
  private selectedId: string | null = null;

  constructor() { this.group.name = 'localization-layer'; }

  setLocalizations(records: readonly InstrumentLocalization[]): void {
    this.clear();
    for (const record of records) {
      const position = record.position;
      if (!position || record.status === 'unmatched' || ![position.x, position.y, position.z].every(Number.isFinite)) continue;
      const id = localizationObjectId(record.localization_id);
      if (this.byId.has(id)) continue;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.4, 0.4),
        new THREE.MeshBasicMaterial({ color: 0xffd166, wireframe: true }));
      mesh.name = id;
      positionToVector3(position, mesh.position);
      markSelectable(mesh, id, 'localization');
      this.byId.set(id, mesh);
      this.group.add(mesh);
    }
    this.setSelectedId(this.selectedId);
  }

  setSelectedId(id: string | null): void {
    this.selectedId = id;
    for (const [key, mesh] of this.byId) {
      mesh.scale.setScalar(key === id ? 1.5 : 1);
      mesh.material.color.setHex(key === id ? 0xffffff : 0xffd166);
    }
  }

  getObject(id: string): THREE.Object3D | undefined { return this.byId.get(id); }

  private clear(): void {
    for (const mesh of this.byId.values()) {
      this.group.remove(mesh);
      mesh.geometry.dispose();
      mesh.material.dispose();
    }
    this.byId.clear();
  }

  dispose(): void { this.clear(); }
}
