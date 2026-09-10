import type * as THREE from 'three';
import type { TwinSelectableKind } from '@digital-twin/contracts';

export const USER_DATA_KEY = 'twinSelectable' as const;

export type SelectableKind = Extract<
  TwinSelectableKind,
  'vehicle' | 'device' | 'event'
>;

export interface SelectableUserData {
  [USER_DATA_KEY]: true;
  twinId: string;
  twinKind: SelectableKind;
}

export function markSelectable(
  object: THREE.Object3D,
  id: string,
  kind: SelectableKind,
): void {
  const data = object.userData as SelectableUserData & Record<string, unknown>;
  data[USER_DATA_KEY] = true;
  data.twinId = id;
  data.twinKind = kind;
}

export function readSelectable(object: THREE.Object3D): SelectableUserData | null {
  const d = object.userData as Partial<SelectableUserData>;
  if (d[USER_DATA_KEY] === true && typeof d.twinId === 'string' && d.twinKind) {
    return d as SelectableUserData;
  }
  return null;
}
