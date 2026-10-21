import { expect, it, vi } from 'vitest';
import { localizationObjectId, type InstrumentLocalization } from '@digital-twin/contracts';
import * as THREE from 'three';
import { LocalizationLayer } from '../src/scene/LocalizationLayer.js';
import { readSelectable } from '../src/interaction/selectable.js';
import { toObjectSelectEvent } from '../src/interaction/Picker.js';

const record: InstrumentLocalization = {
  contract_version: '0.3', localization_id: 'estimate-1', run_id: 'run-1', observation_id: 'observation-1',
  position: { x: 2, y: 3, z: -4 }, heading_deg: null, residual_m: null, status: 'needs_review',
  method: 'ippe_square_conditional_covariance_v1', source_type: 'replay',
  provenance: { source: 'replay', status: 'pending_confirmation' },
  lineage: { source: 'replay', producer: 'fixture', producer_version: '1', derived_from: 'report-1' },
};

it('places only finite positions and reports a distinct localization selection', () => {
  const layer = new LocalizationLayer();
  const id = localizationObjectId(record.localization_id);
  try {
    layer.setLocalizations([record, { ...record, localization_id: 'missing', position: null, status: 'unmatched' },
      { ...record, localization_id: 'invalid', position: { x: NaN, y: 0, z: 0 } }]);
    expect(layer.group.children).toHaveLength(1);
    const marker = layer.getObject(id)!;
    expect(marker.position.toArray()).toEqual([2, 3, -4]);
    expect(readSelectable(marker)?.twinKind).toBe('localization');
    expect(toObjectSelectEvent({ id, kind: 'localization', position: record.position! })?.kind).toBe('localization');
    layer.setSelectedId(id);
    expect(marker.scale.x).toBe(1.5);
    expect(record.status).toBe('needs_review');
  } finally { layer.dispose(); }
});

it('releases replaced geometry and removes the previous page', () => {
  const layer = new LocalizationLayer();
  layer.setLocalizations([record]);
  const mesh = layer.getObject(localizationObjectId(record.localization_id)) as THREE.Mesh;
  const dispose = vi.spyOn(mesh.geometry, 'dispose');
  layer.setLocalizations([]);
  expect(dispose).toHaveBeenCalledOnce();
  expect(layer.group.children).toHaveLength(0);
  layer.dispose();
});
