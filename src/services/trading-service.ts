import { randomUUID } from 'node:crypto';
import { applyFill } from '../domain/trading/accounting.js';
import { isTradingSymbol } from '../domain/trading/types.js';
import type { AgentDecision, Signal } from '../domain/trading/types.js';
import { loadMarks, valuePortfolio } from '../domain/trading/valuation.js';
import type { TradeExecutor } from '../execution/trade-executor.js';
import { ManualMarketDataProvider } from '../market/market-data-provider.js';
import type { MarketDataProvider } from '../market/market-data-provider.js';
import type { TradingRepository } from '../repositories/trading-repository.js';
import { RiskEngine } from '../risk/risk-engine.js';
import { SerialQueue } from '../shared/serial-queue.js';

export class TradingService {
  private readonly queue = new SerialQueue();

  constructor(
    private readonly repository: TradingRepository,
    private readonly executor: TradeExecutor,
    private readonly risk = new RiskEngine(),
    private readonly clock: () => Date = () => new Date(),
    private readonly marketData?: MarketDataProvider,
  ) {}

  submit(signal: Signal) {
    return this.queue.run(async () => {
      const now = this.clock();
      const timestamp = now.toISOString();
      const decision: AgentDecision = {
        id: randomUUID(), signal, status: 'HOLD', tradeId: null,
        rejectionCode: null, rejectionReason: null, createdAt: timestamp,
      };
      if (signal.action === 'HOLD') {
        this.repository.saveDecision(decision);
        return { status: 'held' as const, decisionId: decision.id };
      }
      const snapshot = this.repository.snapshot(now);
      const prices = { ...snapshot.prices };
      if (isTradingSymbol(signal.symbol)) {
        prices[signal.symbol] = { price: signal.price, asOf: timestamp, source: 'signal' };
      }
      const marks = await loadMarks(snapshot.positions, this.marketData ?? new ManualMarketDataProvider(prices));
      const risk = this.risk.evaluate(signal, snapshot, marks, now);
      if (!risk.approved) {
        this.repository.saveDecision({ ...decision, status: 'REJECTED', rejectionCode: risk.code, rejectionReason: risk.reason });
        return { status: 'rejected' as const, decisionId: decision.id, risk: { code: risk.code, reason: risk.reason } };
      }
      // Type narrowing is explicit; only backend-approved symbols reach execution.
      if (!isTradingSymbol(signal.symbol)) throw new Error('Risk engine approved an invalid symbol');
      const fill = await this.executor.execute({ ...signal, symbol: signal.symbol });
      const applied = applyFill(
        snapshot.portfolio, snapshot.positions.find((position) => position.symbol === signal.symbol),
        fill, randomUUID(), timestamp,
      );
      const executedDecision: AgentDecision = { ...decision, status: 'EXECUTED', tradeId: applied.trade.id };
      this.repository.commitTrade({
        ...applied, expectedVersion: snapshot.portfolio.version, decision: executedDecision,
        quote: { price: fill.price, asOf: timestamp, source: 'paper-fill' },
      });
      const updatedPositions = snapshot.positions.filter((position) => position.symbol !== fill.symbol);
      if (applied.position.quantity.isPositive()) updatedPositions.push(applied.position);
      const updatedMarks = new Map(marks).set(fill.symbol, fill.price);
      return {
        status: 'executed' as const, decisionId: decision.id, trade: applied.trade,
        portfolio: valuePortfolio(applied.portfolio, updatedPositions, updatedMarks),
      };
    });
  }
}
