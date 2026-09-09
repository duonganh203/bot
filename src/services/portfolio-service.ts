import type { PriceBook } from '../domain/trading/types.js';
import { loadMarks, markedPositions, valuePortfolio } from '../domain/trading/valuation.js';
import { ManualMarketDataProvider } from '../market/market-data-provider.js';
import type { MarketDataProvider } from '../market/market-data-provider.js';
import type { TradingRepository } from '../repositories/trading-repository.js';
import { DailyRiskService } from '../risk/daily-risk.js';
import { LIMITS } from '../risk/limits.js';
import { randomUUID } from 'node:crypto';
import { toJson } from '../shared/json.js';

export class PortfolioService {
  constructor(
    private readonly repository: TradingRepository,
    private readonly clock: () => Date = () => new Date(),
    private readonly marketData?: MarketDataProvider,
    private readonly dailyRisk = new DailyRiskService(),
  ) {}

  async context(overrides: PriceBook = {}) {
    const now = this.clock();
    const snapshot = this.repository.snapshot(now);
    const quotes = { ...snapshot.prices, ...overrides };
    const marks = await loadMarks(snapshot.positions, this.marketData ?? new ManualMarketDataProvider(quotes));
    const daily = this.dailyRisk.compute(snapshot.tradesToday, now);
    const context = {
      contextId: randomUUID(),
      asOf: now.toISOString(),
      portfolio: valuePortfolio(snapshot.portfolio, snapshot.positions, marks),
      positions: markedPositions(snapshot.positions, marks),
      risk: {
        maxOrderUsd: Number(LIMITS.maxOrderUsd), maxExposureUsd: Number(LIMITS.maxExposureUsd),
        maxDailyLossUsd: Number(LIMITS.maxDailyLossUsd), feeRate: Number(LIMITS.feeRate),
        realizedPnlToday: daily.realizedPnlToday,
        remainingDailyLossBudgetUsd: daily.remainingDailyLossBudgetUsd,
        dailyLossLimitReached: daily.blocked,
        allowedSymbols: ['BTCUSDT', 'ETHUSDT'],
      },
      marketData: { mode: this.marketData ? 'provider' : 'manual', quotes },
      recentTrades: snapshot.recentTrades,
    };
    // Persist exactly the response the caller receives, including temporary query marks.
    this.repository.saveContext({
      id: context.contextId, createdAt: context.asOf,
      portfolioVersion: snapshot.portfolio.version, payload: toJson(context),
    });
    return context;
  }
}
