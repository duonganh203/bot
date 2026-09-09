import { createHash } from 'node:crypto';
import type { Signal } from './types.js';

// Fixed field order and normalized decimals make equivalent validated inputs identical.
export function signalHash(signal: Signal): string {
  const canonical = {
    action: signal.action,
    symbol: signal.symbol ?? null,
    amountUsd: signal.amountUsd?.toFixed() ?? null,
    price: signal.price?.toFixed() ?? null,
    confidence: signal.confidence,
    riskLevel: signal.riskLevel,
    rationale: signal.rationale,
    source: signal.source,
    strategyId: signal.strategyId ?? 'default',
    strategyVersion: signal.strategyVersion ?? 'unversioned',
    contextId: signal.contextId ?? null,
  };
  return createHash('sha256').update(JSON.stringify(canonical)).digest('hex');
}
