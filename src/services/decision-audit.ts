import type { ContextSnapshot, RiskDecision, TradingSnapshot } from '../domain/trading/types.js';
import type { Marks } from '../domain/trading/valuation.js';
import { DailyRiskService } from '../risk/daily-risk.js';
import { LIMITS } from '../risk/limits.js';
import { toJson } from '../shared/json.js';

export function captureDecisionContext(
  snapshot: TradingSnapshot,
  now: Date,
  marks: Marks,
  risk: RiskDecision | null,
  agentContext: ContextSnapshot | undefined,
  marketDataMode: 'manual' | 'provider',
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
    dailyRisk: new DailyRiskService().compute(snapshot.tradesToday, now),
    riskDecision: risk,
    agentContext: agentContext ? {
      id: agentContext.id, capturedAt: agentContext.createdAt, portfolioVersion: agentContext.portfolioVersion,
    } : null,
  }, 'string');
}
