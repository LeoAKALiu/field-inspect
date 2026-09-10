import * as THREE from 'three';
import type { TwinTrajectoryPoint, VehiclePose } from '@digital-twin/contracts';
import { headingDegToQuaternion, positionToVector3 } from '../math/matrix.js';
import { sampleTrajectory, splitTrajectoryByTime } from '../math/trajectory.js';
import { markSelectable } from '../interaction/selectable.js';

export class VehicleLayer {
  readonly group = new THREE.Group();
  private vehicleRoot = new THREE.Group();
  private pastLine: THREE.Line | null = null;
  private futureLine: THREE.Line | null = null;
  private trajectory: TwinTrajectoryPoint[] = [];
  private playbackTime = 0;
  private pose: VehiclePose | null = null;
  private pastColor: number;
  private futureColor: number;
  private vehicleId = 'inspection-vehicle';

  constructor(pastColor = 0xf5a623, futureColor = 0x5dade2) {
    this.pastColor = pastColor;
    this.futureColor = futureColor;
    this.group.name = 'vehicle-layer';
    this.vehicleRoot.name = 'vehicle';
    this.vehicleRoot.add(createProxyVehicle());
    markSelectable(this.vehicleRoot, this.vehicleId, 'vehicle');
    this.vehicleRoot.traverse((c) => {
      if (c instanceof THREE.Mesh) markSelectable(c, this.vehicleId, 'vehicle');
    });
    this.group.add(this.vehicleRoot);
    this.vehicleRoot.visible = false;
  }

  setTrajectory(points: readonly TwinTrajectoryPoint[]): void {
    this.trajectory = points.map((p) => ({
      ...p,
      position: { ...p.position },
    }));
    this.refresh();
  }

  setPlaybackTime(t: number): void {
    this.playbackTime = t;
    this.refresh();
  }

  setVehiclePose(pose: VehiclePose | null): void {
    this.pose = pose
      ? {
          position: { ...pose.position },
          heading_deg: pose.heading_deg,
        }
      : null;
    this.refresh();
  }

  getVehicleObject(): THREE.Object3D {
    return this.vehicleRoot;
  }

  getPosition(): THREE.Vector3 {
    return this.vehicleRoot.position.clone();
  }

  setVisible(visible: boolean): void {
    this.group.visible = visible;
  }

  private refresh(): void {
    if (this.pose && this.trajectory.length === 0) {
      this.applyPose(this.pose.position, this.pose.heading_deg);
      this.clearLines();
      return;
    }

    if (this.trajectory.length > 0) {
      const sample = sampleTrajectory(this.trajectory, this.playbackTime);
      if (sample) {
        this.applyPose(sample.position, sample.heading_deg);
      }
      const { past, future } = splitTrajectoryByTime(this.trajectory, this.playbackTime);
      this.setLine('past', past, this.pastColor);
      this.setLine('future', future, this.futureColor);
      return;
    }

    if (this.pose) {
      this.applyPose(this.pose.position, this.pose.heading_deg);
    } else {
      this.vehicleRoot.visible = false;
      this.clearLines();
    }
  }

  private applyPose(position: { x: number; y: number; z: number }, headingDeg: number): void {
    positionToVector3(position, this.vehicleRoot.position);
    this.vehicleRoot.quaternion.copy(headingDegToQuaternion(headingDeg));
    this.vehicleRoot.visible = true;
  }

  private setLine(
    kind: 'past' | 'future',
    pts: { x: number; y: number; z: number }[],
    color: number,
  ): void {
    const existing = kind === 'past' ? this.pastLine : this.futureLine;
    if (existing) {
      this.group.remove(existing);
      existing.geometry.dispose();
      (existing.material as THREE.Material).dispose();
    }
    if (pts.length < 2) {
      if (kind === 'past') this.pastLine = null;
      else this.futureLine = null;
      return;
    }
    const geo = new THREE.BufferGeometry().setFromPoints(
      pts.map((p) => new THREE.Vector3(p.x, p.y, p.z)),
    );
    const mat = new THREE.LineBasicMaterial({ color });
    const line = new THREE.Line(geo, mat);
    line.name = `trajectory-${kind}`;
    this.group.add(line);
    if (kind === 'past') this.pastLine = line;
    else this.futureLine = line;
  }

  private clearLines(): void {
    this.setLine('past', [], this.pastColor);
    this.setLine('future', [], this.futureColor);
  }

  dispose(): void {
    this.clearLines();
    this.group.remove(this.vehicleRoot);
    this.vehicleRoot.traverse((c) => {
      if (c instanceof THREE.Mesh) {
        c.geometry.dispose();
        const m = c.material;
        if (Array.isArray(m)) m.forEach((x) => x.dispose());
        else m.dispose();
      }
    });
  }
}

function createProxyVehicle(): THREE.Group {
  const g = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.BoxGeometry(2.2, 0.9, 1.2),
    new THREE.MeshStandardMaterial({
      color: 0xe67e22,
      emissive: 0x7a2f05,
      emissiveIntensity: 0.35,
      roughness: 0.65,
      metalness: 0.15,
    }),
  );
  body.position.y = 0.55;
  g.add(body);
  const cabin = new THREE.Mesh(
    new THREE.BoxGeometry(0.9, 0.7, 1.05),
    new THREE.MeshStandardMaterial({ color: 0x2c3e50, roughness: 0.5 }),
  );
  cabin.position.set(0.35, 1.15, 0);
  g.add(cabin);
  const nose = new THREE.Mesh(
    new THREE.ConeGeometry(0.22, 0.55, 10),
    new THREE.MeshStandardMaterial({ color: 0xc0392b }),
  );
  nose.rotation.z = -Math.PI / 2;
  nose.position.set(1.35, 0.55, 0);
  g.add(nose);

  // UI 定位信标：让 200m 级全景视角仍能识别车辆与路线的空间绑定。
  // 它不是车辆物理尺寸的一部分，不参与业务坐标或碰撞计算。
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(2.4, 3.0, 32),
    new THREE.MeshBasicMaterial({
      color: 0x22d3ee,
      transparent: true,
      opacity: 0.82,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  ring.name = 'vehicle-location-ring';
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.08;
  g.add(ring);

  const mast = new THREE.Mesh(
    new THREE.CylinderGeometry(0.07, 0.07, 4.2, 8),
    new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.8 }),
  );
  mast.name = 'vehicle-location-mast';
  mast.position.y = 2.5;
  g.add(mast);

  const beacon = new THREE.Mesh(
    new THREE.SphereGeometry(0.38, 12, 8),
    new THREE.MeshStandardMaterial({
      color: 0xf59e0b,
      emissive: 0xf59e0b,
      emissiveIntensity: 1,
    }),
  );
  beacon.name = 'vehicle-location-beacon';
  beacon.position.y = 4.65;
  g.add(beacon);
  return g;
}
