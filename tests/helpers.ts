import { buildApp } from '../src/app/build-app.js';
import type { AppOptions } from '../src/app/build-app.js';
import { signalSchema } from '../src/app/routes/schemas.js';

export const metadata = { confidence: 0.74, riskLevel: 'LOW', rationale: 'Test signal', source: 'test' };
export const order = (overrides: Record<string, unknown> = {}) => ({
  action: 'BUY', symbol: 'BTCUSDT', amountUsd: 5, price: 100000, ...metadata, ...overrides,
});
export const parsedOrder = (overrides: Record<string, unknown> = {}) => signalSchema.parse(order(overrides));
export const makeApp = (options: Partial<AppOptions> = {}) => buildApp({
  databasePath: ':memory:', clock: () => new Date('2026-09-09T12:00:00.000Z'), ...options,
});

export interface PortfolioResponse {
  initialCapital: number;
  cash: number;
  equity: number;
  realizedPnl: number;
  unrealizedPnl: number;
  positionsValue: number;
  totalFees: number;
  version: number;
  updatedAt: string;
}
export interface TradeResponse {
  id: string;
  side: string;
  symbol: string;
  quantity: number;
  price: number;
  grossUsd: number;
  feeUsd: number;
  netUsd: number;
  realizedPnl: number;
}
export interface ContextResponse {
  contextId: string;
  portfolio: PortfolioResponse;
  positions: { symbol: string; quantity: number; averageEntryPrice: number; currentPrice: number; marketValue: number; unrealizedPnl: number }[];
  risk: { remainingDailyLossBudgetUsd: number; dailyLossLimitReached: boolean; realizedPnlToday: number };
  recentTrades: TradeResponse[];
}
export interface SignalResponse {
  decisionId: string;
  status: string;
  trade: TradeResponse;
  portfolio: PortfolioResponse;
  risk: { code: string };
}
export interface DecisionsResponse {
  decisions: { id: string; status: string; action: string; rejectionCode: string | null; tradeId: string | null }[];
}
