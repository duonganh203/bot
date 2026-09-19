import { sql } from 'drizzle-orm';
import { check, customType, index, integer, real, sqliteTable, text } from 'drizzle-orm/sqlite-core';
import type { Amount } from '../shared/decimal.js';
import { decimal } from '../shared/decimal.js';
import { SYMBOLS } from '../domain/trading/types.js';
import type { SignalResult } from '../domain/trading/types.js';
import type { JsonValue } from '../shared/json.js';

const amount = customType<{ data: Amount; driverData: string }>({
  dataType: () => 'text',
  toDriver: (value) => value.toFixed(),
  fromDriver: (value) => decimal(value),
});

export const portfolios = sqliteTable('portfolio', {
  id: integer('id').primaryKey(),
  initialCapital: amount('initial_capital').notNull(),
  cash: amount('cash').notNull(),
  realizedPnl: amount('realized_pnl').notNull(),
  totalFees: amount('total_fees').notNull(),
  version: integer('version').notNull().default(0),
  createdAt: text('created_at').notNull(),
  updatedAt: text('updated_at').notNull(),
}, (table) => [
  check('singleton_portfolio', sql`${table.id} = 1`),
  check('nonnegative_cash', sql`CAST(${table.cash} AS REAL) >= 0`),
  check('fixed_initial_capital', sql`${table.initialCapital} = '50'`),
]);

export const positions = sqliteTable('positions', {
  symbol: text('symbol', { enum: SYMBOLS }).primaryKey(),
  quantity: amount('quantity').notNull(),
  costBasisUsd: amount('cost_basis_usd').notNull(),
  entryFeesUsd: amount('entry_fees_usd').notNull(),
  averageEntryPrice: amount('average_entry_price').notNull(),
  createdAt: text('created_at').notNull(),
  updatedAt: text('updated_at').notNull(),
}, (table) => [
  check('position_symbol', sql`${table.symbol} IN ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT')`),
  check('positive_quantity', sql`CAST(${table.quantity} AS REAL) > 0`),
  check('nonnegative_cost', sql`CAST(${table.costBasisUsd} AS REAL) >= 0`),
  check('nonnegative_entry_fees', sql`CAST(${table.entryFeesUsd} AS REAL) >= 0`),
]);

export const trades = sqliteTable('trades', {
  sequence: integer('sequence').primaryKey({ autoIncrement: true }),
  id: text('id').notNull().unique(),
  symbol: text('symbol', { enum: SYMBOLS }).notNull(),
  side: text('side', { enum: ['BUY', 'SELL'] }).notNull(),
  quantity: amount('quantity').notNull(),
  price: amount('price').notNull(),
  grossUsd: amount('gross_usd').notNull(),
  feeUsd: amount('fee_usd').notNull(),
  netUsd: amount('net_usd').notNull(),
  realizedPnl: amount('realized_pnl').notNull(),
  createdAt: text('created_at').notNull(),
}, (table) => [
  index('trades_created_at_idx').on(table.createdAt),
  index('trades_symbol_sequence_idx').on(table.symbol, table.sequence),
  check('trade_symbol', sql`${table.symbol} IN ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT')`),
  check('trade_side', sql`${table.side} IN ('BUY', 'SELL')`),
  check('trade_positive_quantity', sql`CAST(${table.quantity} AS REAL) > 0`),
  check('trade_positive_price', sql`CAST(${table.price} AS REAL) > 0`),
  check('trade_order_limit', sql`CAST(${table.grossUsd} AS REAL) > 0 AND CAST(${table.grossUsd} AS REAL) <= 5`),
]);

export const contextSnapshots = sqliteTable('context_snapshots', {
  id: text('id').primaryKey(),
  portfolioVersion: integer('portfolio_version').notNull(),
  payload: text('payload', { mode: 'json' }).$type<JsonValue>().notNull(),
  createdAt: text('created_at').notNull(),
}, (table) => [index('context_snapshots_created_at_idx').on(table.createdAt)]);

export const agentDecisions = sqliteTable('agent_decisions', {
  sequence: integer('sequence').primaryKey({ autoIncrement: true }),
  id: text('id').notNull().unique(),
  action: text('action', { enum: ['BUY', 'SELL', 'HOLD'] }).notNull(),
  symbol: text('symbol'),
  amountUsd: amount('amount_usd'),
  price: amount('price'),
  confidence: real('confidence').notNull(),
  riskLevel: text('risk_level', { enum: ['LOW', 'MEDIUM', 'HIGH'] }).notNull(),
  rationale: text('rationale').notNull(),
  source: text('source').notNull(),
  strategyId: text('strategy_id').notNull().default('default'),
  strategyVersion: text('strategy_version').notNull().default('unversioned'),
  contextId: text('context_id').references(() => contextSnapshots.id),
  executionContext: text('execution_context', { mode: 'json' }).$type<JsonValue>(),
  status: text('status', { enum: ['HOLD', 'EXECUTED', 'REJECTED'] }).notNull(),
  tradeId: text('trade_id').references(() => trades.id).unique(),
  rejectionCode: text('rejection_code'),
  rejectionReason: text('rejection_reason'),
  createdAt: text('created_at').notNull(),
}, (table) => [
  index('decisions_strategy_created_at_idx').on(table.strategyId, table.strategyVersion, table.createdAt),
  index('decisions_created_at_idx').on(table.createdAt),
  check('decision_action', sql`${table.action} IN ('BUY', 'SELL', 'HOLD')`),
  check('decision_confidence', sql`${table.confidence} BETWEEN 0 AND 1`),
  check('decision_consistency', sql`
    (${table.status} = 'HOLD' AND ${table.action} = 'HOLD' AND ${table.tradeId} IS NULL AND ${table.rejectionCode} IS NULL)
    OR (${table.status} = 'EXECUTED' AND ${table.action} IN ('BUY', 'SELL') AND ${table.tradeId} IS NOT NULL AND ${table.rejectionCode} IS NULL)
    OR (${table.status} = 'REJECTED' AND ${table.action} IN ('BUY', 'SELL') AND ${table.tradeId} IS NULL AND ${table.rejectionCode} IS NOT NULL AND ${table.rejectionReason} IS NOT NULL)
  `),
]);

export const signalReceipts = sqliteTable('signal_receipts', {
  key: text('key').primaryKey(),
  requestHash: text('request_hash').notNull(),
  decisionId: text('decision_id').notNull().unique().references(() => agentDecisions.id),
  result: text('result', { mode: 'json' }).$type<SignalResult>().notNull(),
  createdAt: text('created_at').notNull(),
});

export const marketPrices = sqliteTable('market_prices', {
  symbol: text('symbol', { enum: SYMBOLS }).primaryKey(),
  price: amount('price').notNull(),
  asOf: text('as_of').notNull(),
  source: text('source').notNull(),
}, (table) => [
  check('market_symbol', sql`${table.symbol} IN ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT')`),
  check('market_positive_price', sql`CAST(${table.price} AS REAL) > 0`),
]);
