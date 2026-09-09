// Hard-coded backend policy. Neither env nor signals can override these values.
export const LIMITS = Object.freeze({
  initialCapitalUsd: '50',
  maxOrderUsd: '5',
  maxExposureUsd: '20',
  maxDailyLossUsd: '3',
  feeRate: '0.001',
});
