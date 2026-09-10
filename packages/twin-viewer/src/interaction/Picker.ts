import * as THREE from 'three';
import type { TwinObjectSelectEvent, TwinSelectableKind } from '@digital-twin/contracts';
import { readSelectable } from './selectable.js';
import { vector3ToPosition } from '../math/matrix.js';

/** Internal pick hit (richer than contracts coordinate-only event). */
export interface EnginePickHit {
  kind: TwinSelectableKind;
  id?: string;
  position: { x: number; y: number; z: number };
}

export class Picker {
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly camera: THREE.Camera;
  private readonly dom: HTMLElement;
  private readonly meshRoot: THREE.Object3D;
  private readonly pointsRoot: THREE.Object3D;
  private readonly entityRoot: THREE.Object3D;
  private disposed = false;

  private readonly onClick: (ev: MouseEvent) => void;
  private readonly onDblClick: (ev: MouseEvent) => void;

  onPick: ((result: EnginePickHit) => void) | null = null;
  onFocusRequest: ((objectId: string) => void) | null = null;

  constructor(
    camera: THREE.Camera,
    dom: HTMLElement,
    roots: { mesh: THREE.Object3D; points: THREE.Object3D; entities: THREE.Object3D },
  ) {
    this.camera = camera;
    this.dom = dom;
    this.meshRoot = roots.mesh;
    this.pointsRoot = roots.points;
    this.entityRoot = roots.entities;
    this.raycaster.params.Points = { threshold: 0.35 };
    this.onClick = (ev) => this.handlePointer(ev, false);
    this.onDblClick = (ev) => this.handlePointer(ev, true);
    this.dom.addEventListener('click', this.onClick);
    this.dom.addEventListener('dblclick', this.onDblClick);
  }

  setPointThreshold(meters: number): void {
    this.raycaster.params.Points = { threshold: meters };
  }

  /** NDC pick (−1..1). Returns null on empty miss — never fabricates. */
  pickNdc(ndcX: number, ndcY: number): EnginePickHit | null {
    this.pointer.set(ndcX, ndcY);
    this.raycaster.setFromCamera(this.pointer, this.camera);

    const entityHits = this.raycaster.intersectObject(this.entityRoot, true);
    for (const hit of entityHits) {
      let obj: THREE.Object3D | null = hit.object;
      while (obj) {
        const sel = readSelectable(obj);
        if (sel) {
          return {
            kind: sel.twinKind,
            id: sel.twinId,
            position: vector3ToPosition(hit.point),
          };
        }
        obj = obj.parent;
      }
    }

    if (this.meshRoot.visible) {
      const meshHits = this.raycaster.intersectObject(this.meshRoot, true);
      if (meshHits[0]) {
        return {
          kind: 'mesh',
          position: vector3ToPosition(meshHits[0].point),
        };
      }
    }

    if (this.pointsRoot.visible) {
      const pointHits = this.raycaster.intersectObject(this.pointsRoot, true);
      if (pointHits[0]) {
        return {
          kind: 'point',
          position: vector3ToPosition(pointHits[0].point),
        };
      }
    }

    return null;
  }

  private handlePointer(ev: MouseEvent, isDouble: boolean): void {
    if (this.disposed) return;
    const rect = this.dom.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const ndcX = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    const ndcY = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    const result = this.pickNdc(ndcX, ndcY);
    if (!result) return;

    if (isDouble && result.id) {
      this.onFocusRequest?.(result.id);
    }

    this.onPick?.(result);
  }

  dispose(): void {
    this.disposed = true;
    this.dom.removeEventListener('click', this.onClick);
    this.dom.removeEventListener('dblclick', this.onDblClick);
    this.onPick = null;
    this.onFocusRequest = null;
  }
}

export function toObjectSelectEvent(hit: EnginePickHit): TwinObjectSelectEvent | null {
  if (!hit.id) return null;
  if (hit.kind !== 'device' && hit.kind !== 'event' && hit.kind !== 'vehicle') return null;
  return {
    id: hit.id,
    kind: hit.kind,
    position: hit.position,
  };
}
