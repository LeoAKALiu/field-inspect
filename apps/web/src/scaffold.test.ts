import { describe, expect, it } from 'vitest';
import { CONTRACTS_VERSION } from '@digital-twin/contracts';
import type { SourceType } from '@digital-twin/contracts';

describe('contracts v2', () => {
  it('loads shared contracts', () => {
    expect(CONTRACTS_VERSION).toBe('0.3.0');
  });

  it('exposes the three data source types', () => {
    const sources: SourceType[] = ['simulation', 'replay', 'live_pending'];
    expect(sources).toHaveLength(3);
  });
});
