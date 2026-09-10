import { describe, expect, it } from 'vitest';
import { recordedRunLabel } from './labels';

describe('recordedRunLabel', () => {
  it('labels demonstration, pending, accepted, and withdrawn independently', () => {
    expect(
      recordedRunLabel({ run_kind: 'demonstration', acceptance_state: 'not_applicable' }),
    ).toBe('演示运行');
    expect(
      recordedRunLabel({ run_kind: 'recorded', acceptance_state: 'pending_acceptance' }),
    ).toBe('现场实录，待验收');
    expect(recordedRunLabel({ run_kind: 'recorded', acceptance_state: 'accepted' })).toBe(
      '现场已验收',
    );
    expect(recordedRunLabel({ run_kind: 'recorded', acceptance_state: 'withdrawn' })).toBe(
      '现场实录，已撤销',
    );
  });
});
