import type { Trade } from '../domain/trading/types.js';
import { D, decimal } from '../shared/decimal.js';
import { LIMITS } from './limits.js';

export function utcDayRange(now: Date): { start: string; end: string } {
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const end = new Date(start.getTime() + 86_400_000);
  return { start: start.toISOString(), end: end.toISOString() };
}

export class DailyRiskService {
  compute(trades: readonly Trade[], now: Date) {
    const { start, end } = utcDayRange(now);
    const realizedPnlToday = trades
      .filter((trade) => trade.createdAt >= start && trade.createdAt < end)
      .reduce((sum, trade) => sum.plus(trade.realizedPnl), decimal(0));
    return {
      realizedPnlToday,
      blocked: realizedPnlToday.lte(decimal(LIMITS.maxDailyLossUsd).neg()),
      remainingDailyLossBudgetUsd: D.max(0, decimal(LIMITS.maxDailyLossUsd).plus(realizedPnlToday)),
    };
  }
}
