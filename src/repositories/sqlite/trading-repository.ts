import { and, desc, eq, gte, lt } from 'drizzle-orm';
import type { DatabaseConnection } from '../../db/client.js';
import { agentDecisions, marketPrices, portfolios, positions, trades } from '../../db/schema.js';
import type { AgentDecision, PriceBook, TradingSnapshot } from '../../domain/trading/types.js';
import { utcDayRange } from '../../risk/daily-risk.js';
import { ConcurrentUpdateError } from '../../shared/errors.js';
import type { CommitTrade, HistoryFilter, TradingRepository } from '../trading-repository.js';

function decisionRow(decision: AgentDecision) {
  return {
    ...decision.signal,
    symbol: decision.signal.symbol ?? null,
    amountUsd: decision.signal.amountUsd ?? null,
    price: decision.signal.price ?? null,
    id: decision.id, status: decision.status, tradeId: decision.tradeId,
    rejectionCode: decision.rejectionCode, rejectionReason: decision.rejectionReason,
    createdAt: decision.createdAt,
  };
}

export class SqliteTradingRepository implements TradingRepository {
  constructor(private readonly connection: DatabaseConnection) {}

  snapshot(now: Date): TradingSnapshot {
    return this.connection.db.transaction((tx) => {
      const portfolio = tx.select().from(portfolios).where(eq(portfolios.id, 1)).get();
      if (!portfolio) throw new Error('Portfolio is not initialized');
      const prices: PriceBook = {};
      for (const quote of tx.select().from(marketPrices).all()) prices[quote.symbol] = quote;
      const day = utcDayRange(now);
      return {
        portfolio,
        positions: tx.select().from(positions).all(),
        prices,
        tradesToday: tx.select().from(trades)
          .where(and(gte(trades.createdAt, day.start), lt(trades.createdAt, day.end))).all(),
        recentTrades: tx.select().from(trades).orderBy(desc(trades.sequence)).limit(20).all(),
      };
    });
  }

  saveDecision(decision: AgentDecision): void {
    this.connection.db.insert(agentDecisions).values(decisionRow(decision)).run();
  }

  commitTrade(commit: CommitTrade): void {
    this.connection.db.transaction((tx) => {
      const updated = tx.update(portfolios).set(commit.portfolio)
        .where(and(eq(portfolios.id, 1), eq(portfolios.version, commit.expectedVersion))).run();
      if (updated.changes !== 1) throw new ConcurrentUpdateError();
      if (commit.position.quantity.isZero()) {
        tx.delete(positions).where(eq(positions.symbol, commit.position.symbol)).run();
      } else {
        tx.insert(positions).values(commit.position)
          .onConflictDoUpdate({ target: positions.symbol, set: commit.position }).run();
      }
      tx.insert(trades).values(commit.trade).run();
      tx.insert(agentDecisions).values(decisionRow(commit.decision)).run();
      tx.insert(marketPrices).values({ symbol: commit.trade.symbol, ...commit.quote })
        .onConflictDoUpdate({ target: marketPrices.symbol, set: commit.quote }).run();
    }, { behavior: 'immediate' });
  }

  listTrades(filter: HistoryFilter) {
    return this.connection.db.select().from(trades)
      .where(filter.symbol ? eq(trades.symbol, filter.symbol) : undefined)
      .orderBy(desc(trades.sequence)).limit(filter.limit).all();
  }

  listDecisions(limit: number) {
    return this.connection.db.select().from(agentDecisions)
      .orderBy(desc(agentDecisions.sequence)).limit(limit).all();
  }
}
