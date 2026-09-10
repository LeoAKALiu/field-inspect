import { describe, expect, it } from 'vitest';
import { VehicleLayer } from '../src/scene/VehicleLayer.js';

describe('VehicleLayer location beacon', () => {
  it('keeps the vehicle visible in a scene-scale overview without changing its pose', () => {
    const layer = new VehicleLayer();
    layer.setVehiclePose({ position: { x: 12, y: 3, z: -8 }, heading_deg: 45 });

    const vehicle = layer.getVehicleObject();
    expect(vehicle.visible).toBe(true);
    expect(vehicle.position.toArray()).toEqual([12, 3, -8]);
    expect(vehicle.getObjectByName('vehicle-location-ring')).toBeTruthy();
    expect(vehicle.getObjectByName('vehicle-location-mast')).toBeTruthy();
    expect(vehicle.getObjectByName('vehicle-location-beacon')).toBeTruthy();

    layer.dispose();
  });
});
