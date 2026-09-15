import type { ContextSnapshot, RiskDecision, TradingSnapshot } from '../domain/trading/types.js';
import type { Marks } from '../domain/trading/valuation.js';
import { DailyRiskService } from '../risk/daily-risk.js';
import { LIMITS } from '../risk/limits.js';
import type { RiskPolicy } from '../risk/limits.js';
import { MAX_EQUITY_LOSS_USD } from '../risk/limits.js';
import { toJson } from '../shared/json.js';

export function captureDecisionContext(
  snapshot: TradingSnapshot,
  now: Date,
  marks: Marks,
  risk: RiskDecision | null,
  agentContext: ContextSnapshot | undefined,
  marketDataMode: 'manual' | 'provider',
  riskPolicy: RiskPolicy = 'legacy-v1',
) {
  return toJson({
    schemaVersion: 1,
    capturedAt: now.toISOString(),
    portfolio: snapshot.portfolio,
    positions: snapshot.positions,
    quotes: snapshot.prices,
    marks: Object.fromEntries(marks),
    marketDataMode,
    limits: LIMITS,
    riskPolicy,
    ...(riskPolicy === 'reduce-only-v2' ? { maxEquityLossUsd: MAX_EQUITY_LOSS_USD } : {}),
    dailyRisk: new DailyRiskService().compute(snapshot.tradesToday, now),
    riskDecision: risk,
    agentContext: agentContext ? {
      id: agentContext.id, capturedAt: agentContext.createdAt, portfolioVersion: agentContext.portfolioVersion,
    } : null,
  }, 'string');
}
