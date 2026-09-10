import { describe, expect, it } from 'vitest';
import {
  CONTRACTS_VERSION,
  DOMAIN_CONTRACT_VERSION,
  type AssetMatch,
  type DataLineage,
  type InspectionRunOutcome,
  type InspectionStation,
  type InstrumentLocalization,
  type InstrumentObservation,
  type InstrumentReading,
  type StationAttempt,
} from './index';
import observationSimulation from '../../../data/contract-fixtures/instrument-v0.3/observation-marker-proxy-simulation.json';
import observationFixture from '../../../data/contract-fixtures/instrument-v0.3/observation-synthetic-fixture.json';
import localizationMatched from '../../../data/contract-fixtures/instrument-v0.3/localization-matched.json';
import localizationUnmatched from '../../../data/contract-fixtures/instrument-v0.3/localization-unmatched.json';
import localizationNeedsReview from '../../../data/contract-fixtures/instrument-v0.3/localization-needs-review.json';
import assetMatchMatched from '../../../data/contract-fixtures/instrument-v0.3/asset-match-matched.json';
import assetMatchUnmatched from '../../../data/contract-fixtures/instrument-v0.3/asset-match-unmatched.json';
import assetMatchNeedsReview from '../../../data/contract-fixtures/instrument-v0.3/asset-match-needs-review.json';
import readingHistorical from '../../../data/contract-fixtures/instrument-v0.3/reading-historical-replay.json';
import readingExpired from '../../../data/contract-fixtures/instrument-v0.3/reading-expired.json';
import stationRegistered from '../../../data/contract-fixtures/instrument-v0.3/station-registered.json';
import attemptFailed from '../../../data/contract-fixtures/instrument-v0.3/attempt-failed-then-retried.json';
import attemptSuccess from '../../../data/contract-fixtures/instrument-v0.3/attempt-success-after-retry.json';
import attemptSkipped from '../../../data/contract-fixtures/instrument-v0.3/attempt-skipped.json';
import runOutcomeCompleted from '../../../data/contract-fixtures/instrument-v0.3/run-outcome-completed.json';
import runOutcomeExceptions from '../../../data/contract-fixtures/instrument-v0.3/run-outcome-completed-with-exceptions.json';
import runOutcomeAborted from '../../../data/contract-fixtures/instrument-v0.3/run-outcome-aborted.json';
import malformedMissingConfidence from '../../../data/contract-fixtures/instrument-v0.3/malformed-observation-missing-confidence.json';
import malformedEmptyMetrics from '../../../data/contract-fixtures/instrument-v0.3/malformed-reading-empty-metrics.json';
import malformedUnmatchedPosition from '../../../data/contract-fixtures/instrument-v0.3/malformed-localization-unmatched-with-position.json';
import malformedSuccessReason from '../../../data/contract-fixtures/instrument-v0.3/malformed-attempt-success-with-reason.json';
import unknownContractVersion from '../../../data/contract-fixtures/instrument-v0.3/unknown-contract-version.json';
import domainSchema from '../../../packages/contracts/domain.schema.json';

const VALID_FIXTURES: Array<Record<string, unknown>> = [
  observationSimulation,
  observationFixture,
  localizationMatched,
  localizationUnmatched,
  localizationNeedsReview,
  assetMatchMatched,
  assetMatchUnmatched,
  assetMatchNeedsReview,
  readingHistorical,
  readingExpired,
  stationRegistered,
  attemptFailed,
  attemptSuccess,
  attemptSkipped,
  runOutcomeCompleted,
  runOutcomeExceptions,
  runOutcomeAborted,
];

const PROVENANCE = { source: 'simulation', status: 'simulated' } as const;
const LINEAGE: DataLineage = {
  source: 'simulation',
  producer: 'marker_proxy',
  producer_version: 'apriltag_36h11_v1',
  derived_from: null,
};

describe('domain contract v0.3', () => {
  it('exports the v0.3 contract versions', () => {
    expect(CONTRACTS_VERSION).toBe('0.3.0');
    expect(DOMAIN_CONTRACT_VERSION).toBe('0.3');
  });

  it('types a complete golden success path (compile-time public boundary)', () => {
    const observation: InstrumentObservation = {
      contract_version: '0.3',
      observation_id: 'obs-v03-0001',
      run_id: 'run-v03-golden-1',
      captured_at: '2026-08-25T03:00:01.200Z',
      image_region: { x_min: 0.41, y_min: 0.35, x_max: 0.52, y_max: 0.58, image_width: 1280, image_height: 1024 },
      detector: { kind: 'marker_proxy', version: 'apriltag_36h11_v1' },
      confidence: 0.97,
      calibration_ref: 'camera-calib-hik-cs050-v1',
      source_type: 'simulation',
      provenance: PROVENANCE,
      lineage: LINEAGE,
    };
    const localization: InstrumentLocalization = {
      contract_version: '0.3',
      localization_id: 'loc-v03-0001',
      run_id: 'run-v03-golden-1',
      observation_id: observation.observation_id,
      status: 'matched',
      position: { x: 12.35, y: 1.42, z: 0.0 },
      heading_deg: 90.5,
      method: 'marker_pose_scene_transform',
      residual_m: 0.012,
      source_type: 'simulation',
      provenance: PROVENANCE,
      lineage: LINEAGE,
    };
    const assetMatch: AssetMatch = {
      contract_version: '0.3',
      match_id: 'match-v03-0001',
      run_id: 'run-v03-golden-1',
      localization_id: localization.localization_id,
      status: 'matched',
      asset_id: 'ast-delam-001',
      candidate_asset_ids: [],
      rule_version: 'registry_unique_v1',
      source_type: 'simulation',
      provenance: PROVENANCE,
      lineage: LINEAGE,
    };
    const reading: InstrumentReading = {
      contract_version: '0.3',
      reading_id: 'read-v03-0001',
      run_id: 'run-v03-golden-1',
      asset_id: assetMatch.asset_id as string,
      captured_at: '2026-08-25T03:00:02.000Z',
      metrics: [
        { name: 'deep_base', value: 12.4, unit: 'mm' },
        { name: 'shallow_base', value: 3.1, unit: 'mm' },
        { name: 'delta', value: 9.3, unit: 'mm' },
      ],
      quality: 'good',
      valid_until: '2026-12-31T00:00:00Z',
      source_type: 'replay',
      provenance: { source: 'replay', status: 'pending_confirmation' },
      lineage: { source: 'replay', producer: 'inspection_trajectory_export', producer_version: '1.0.0', derived_from: 'run-v03-golden-1' },
    };
    expect(reading.metrics.map((metric) => metric.name)).toEqual(['deep_base', 'shallow_base', 'delta']);
    expect(assetMatch.status).toBe('matched');
  });

  it('types localization/matching as independent objects with three states', () => {
    const unmatched: InstrumentLocalization = {
      contract_version: '0.3',
      localization_id: 'loc-v03-0002',
      run_id: 'run-v03-golden-1',
      observation_id: 'obs-v03-0002',
      status: 'unmatched',
      position: null,
      heading_deg: null,
      method: 'marker_pose_scene_transform',
      residual_m: null,
      source_type: 'simulation',
      provenance: PROVENANCE,
      lineage: LINEAGE,
    };
    const needsReview: AssetMatch = {
      contract_version: '0.3',
      match_id: 'match-v03-0003',
      run_id: 'run-v03-golden-1',
      localization_id: unmatched.localization_id,
      status: 'needs_review',
      asset_id: null,
      candidate_asset_ids: ['ast-delam-002', 'ast-delam-003'],
      rule_version: 'registry_unique_v1',
      source_type: 'simulation',
      provenance: PROVENANCE,
      lineage: LINEAGE,
    };
    expect(unmatched.status).toBe('unmatched');
    expect(needsReview.asset_id).toBeNull();
    expect(needsReview.candidate_asset_ids.length).toBeGreaterThan(0);
  });

  it('types station attempts and traceable run outcomes', () => {
    const station: InspectionStation = {
      contract_version: '0.3',
      station_id: 'stn-001',
      scene_id: 'scene-001',
      name: '1 号支巷离层仪观测站',
      target_pose: { x: 12.3, y: 1.4, z: 0.0 },
      target_heading_deg: 90.0,
      registered_instruments: ['ast-delam-001'],
      description: null,
      source_type: 'simulation',
      provenance: PROVENANCE,
      lineage: LINEAGE,
    };
    const retry: StationAttempt = {
      contract_version: '0.3',
      attempt_id: 'att-v03-0002',
      run_id: 'run-v03-golden-1',
      station_id: station.station_id,
      attempt_seq: 2,
      started_at: '2026-08-25T03:00:00.000Z',
      ended_at: '2026-08-25T03:00:10.000Z',
      result: 'success',
      reason_code: null,
      reason_detail: null,
      source_type: 'simulation',
      provenance: PROVENANCE,
      lineage: LINEAGE,
    };
    const outcome: InspectionRunOutcome = {
      contract_version: '0.3',
      run_id: 'run-v03-golden-1',
      final_status: 'completed_with_exceptions',
      skipped_station_ids: ['stn-002'],
      retried_station_ids: [station.station_id],
      abort: null,
      source_type: 'simulation',
      provenance: PROVENANCE,
      lineage: LINEAGE,
    };
    expect(retry.attempt_seq).toBeGreaterThan(1);
    expect(outcome.final_status).toBe('completed_with_exceptions');
  });

  it('every valid golden fixture carries contract_version 0.3 and a lineage', () => {
    for (const fixture of VALID_FIXTURES) {
      expect(fixture.contract_version).toBe(DOMAIN_CONTRACT_VERSION);
      expect(fixture.lineage).toBeDefined();
      expect(fixture.source_type).toBeDefined();
      expect(fixture.provenance).toBeDefined();
    }
    expect(observationFixture.lineage.source).toBe('synthetic_fixture');
    expect(readingHistorical.lineage.source).toBe('replay');
    expect(stationRegistered.lineage.source).toBe('simulation');
  });

  it('golden set covers named metrics, skip/retry traceability, and stale readings', () => {
    expect(readingHistorical.metrics.map((metric) => metric.name)).toEqual([
      'deep_base',
      'shallow_base',
      'delta',
    ]);
    expect(readingExpired.valid_until).toBe('2026-06-01T00:00:00Z');
    expect(attemptFailed.attempt_seq).toBe(1);
    expect(attemptSuccess.attempt_seq).toBeGreaterThan(1);
    expect(attemptSkipped.result).toBe('skipped');
    expect(runOutcomeExceptions.final_status).toBe('completed_with_exceptions');
    expect(runOutcomeExceptions.skipped_station_ids).toEqual(['stn-002']);
    expect(runOutcomeExceptions.retried_station_ids).toEqual(['stn-001']);
    expect(runOutcomeCompleted.skipped_station_ids).toEqual([]);
    expect(runOutcomeAborted.abort?.reason_code).toBe('safety_stop');
  });

  it('keeps malformed/unknown-version fixtures explicitly invalid-shaped', () => {
    expect('confidence' in malformedMissingConfidence).toBe(false);
    expect(malformedEmptyMetrics.metrics).toEqual([]);
    expect(malformedUnmatchedPosition.status).toBe('unmatched');
    expect(malformedUnmatchedPosition.position).not.toBeNull();
    expect(malformedSuccessReason.result).toBe('success');
    expect(malformedSuccessReason.reason_code).not.toBeNull();
    expect(unknownContractVersion.contract_version).toBe('0.2');
  });

  it('locks v0.3 schema required fields, enums, and boundary wording', () => {
    const defs = domainSchema.$defs;

    expect(defs.InstrumentDetectorKind.const).toBe('marker_proxy');
    expect(defs.DataSourceKind.enum).toEqual(['synthetic_fixture', 'simulation', 'replay', 'live_pending']);
    expect(defs.ResolutionStatus.enum).toEqual(['matched', 'unmatched', 'needs_review']);
    expect(defs.InstrumentMetricName.enum).toEqual(['deep_base', 'shallow_base', 'delta']);
    expect(defs.StationAttemptResult.enum).toEqual(['success', 'failed', 'skipped']);
    expect(defs.InspectionRunFinalStatus.enum).toEqual(['completed', 'completed_with_exceptions', 'aborted']);

    expect(defs.InstrumentObservation.required).toContain('confidence');
    expect(defs.InstrumentObservation.required).toContain('calibration_ref');
    expect(defs.AssetMatch.required).toContain('candidate_asset_ids');
    expect(defs.InstrumentReading.required).toContain('metrics');
    expect(defs.InspectionRunOutcome.required).toContain('final_status');

    // marker_proxy 的语义边界必须在 schema 描述中显式标记。
    expect(defs.InstrumentDetectorKind.description).toContain('不等于通用图像识别');
    expect(defs.AssetMatch.description).toContain('不产生任何已确认的客户设备 ID');
  });
});
