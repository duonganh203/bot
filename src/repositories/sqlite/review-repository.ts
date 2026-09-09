import { and, desc, eq, gte, lte, sql } from 'drizzle-orm';
import type { DatabaseConnection } from '../../db/client.js';
import { agentDecisions as decisions, contextSnapshots, signalReceipts, trades } from '../../db/schema.js';
import type { ReviewFilter, ReviewRepository } from '../review-repository.js';

export class SqliteReviewRepository implements ReviewRepository {
  constructor(private readonly connection: DatabaseConnection) {}

  observations(filter: ReviewFilter, limit: number) {
    // Project only review evidence; do not load potentially large audit JSON blobs.
    return this.connection.db.select({
      id: decisions.id, createdAt: decisions.createdAt, action: decisions.action, status: decisions.status,
      rejectionCode: decisions.rejectionCode, strategyId: decisions.strategyId, strategyVersion: decisions.strategyVersion,
      contextId: decisions.contextId, idempotencyKey: signalReceipts.key,
      contextCreatedAt: contextSnapshots.createdAt, contextPortfolioVersion: contextSnapshots.portfolioVersion,
      executionPortfolioVersion: sql<number | null>`json_extract(${decisions.executionContext}, '$.portfolio.version')`,
      feeUsd: trades.feeUsd, realizedPnl: trades.realizedPnl,
    }).from(decisions)
      .leftJoin(contextSnapshots, eq(decisions.contextId, contextSnapshots.id))
      .leftJoin(signalReceipts, eq(decisions.id, signalReceipts.decisionId))
      .leftJoin(trades, eq(decisions.tradeId, trades.id))
      .where(and(gte(decisions.createdAt, filter.from), lte(decisions.createdAt, filter.to),
        filter.strategyId ? eq(decisions.strategyId, filter.strategyId) : undefined,
        filter.strategyVersion ? eq(decisions.strategyVersion, filter.strategyVersion) : undefined))
      .orderBy(desc(decisions.createdAt), desc(decisions.sequence)).limit(limit).all();
  }
}
