import type { HistoryFilter, TradingRepository } from '../repositories/trading-repository.js';
import { AppError } from '../shared/errors.js';

export class HistoryService {
  constructor(private readonly repository: TradingRepository) {}

  trades(filter: HistoryFilter) {
    return this.repository.listTrades(filter);
  }

  decisions(limit: number) {
    return this.repository.listDecisions(limit);
  }

  decision(id: string) {
    const decision = this.repository.getDecision(id);
    if (!decision) throw new AppError('DECISION_NOT_FOUND', 'Decision does not exist.', 404);
    return { decision, agentContext: decision.contextId ? this.repository.getContext(decision.contextId)?.payload ?? null : null };
  }

  context(id: string) {
    const context = this.repository.getContext(id);
    if (!context) throw new AppError('CONTEXT_NOT_FOUND', 'Context snapshot does not exist.', 404);
    return context.payload;
  }
}
