// Hard-coded backend policy. Neither env nor signals can override these values.
export const LIMITS = Object.freeze({
  initialCapitalUsd: '50',
  maxOrderUsd: '5',
  maxExposureUsd: '20',
  maxDailyLossUsd: '3',
  feeRate: '0.001',
});

export type RiskPolicy = 'legacy-v1' | 'reduce-only-v2';
export const MAX_EQUITY_LOSS_USD = '3';
export const equityFloor = (initialCapital: import('../shared/decimal.js').Amount) => initialCapital.minus(MAX_EQUITY_LOSS_USD);
