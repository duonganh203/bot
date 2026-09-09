import type { HistoryFilter, TradingRepository } from '../repositories/trading-repository.js';

export class HistoryService {
  constructor(private readonly repository: TradingRepository) {}

  trades(filter: HistoryFilter) {
    return this.repository.listTrades(filter);
  }

  decisions(limit: number) {
    return this.repository.listDecisions(limit);
  }
}
