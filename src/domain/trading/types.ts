import type { Amount } from '../../shared/decimal.js';

export const SYMBOLS = ['BTCUSDT', 'ETHUSDT'] as const;
export type TradingSymbol = (typeof SYMBOLS)[number];
export const isTradingSymbol = (symbol: string): symbol is TradingSymbol =>
  SYMBOLS.some((allowed) => allowed === symbol);
export type Side = 'BUY' | 'SELL';
export type TradingAction = Side | 'HOLD';
export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH';

interface SignalMetadata {
  confidence: number;
  riskLevel: RiskLevel;
  rationale: string;
  source: string;
}
export interface OrderSignal extends SignalMetadata {
  action: Side;
  symbol: string;
  amountUsd: Amount;
  price: Amount;
}
export interface HoldSignal extends SignalMetadata {
  action: 'HOLD';
  symbol?: string | undefined;
  amountUsd?: Amount | undefined;
  price?: Amount | undefined;
}
export type Signal = OrderSignal | HoldSignal;

export interface Portfolio {
  id: number;
  initialCapital: Amount;
  cash: Amount;
  realizedPnl: Amount;
  totalFees: Amount;
  version: number;
  createdAt: string;
  updatedAt: string;
}
export interface Position {
  symbol: TradingSymbol;
  quantity: Amount;
  costBasisUsd: Amount;
  entryFeesUsd: Amount;
  averageEntryPrice: Amount;
  createdAt: string;
  updatedAt: string;
}
export interface Trade {
  id: string;
  symbol: TradingSymbol;
  side: Side;
  quantity: Amount;
  price: Amount;
  grossUsd: Amount;
  feeUsd: Amount;
  // Positive cash magnitude: BUY debit / SELL credit.
  netUsd: Amount;
  realizedPnl: Amount;
  createdAt: string;
}
export type RiskCode =
  | 'ORDER_TOO_LARGE'
  | 'MAX_EXPOSURE'
  | 'DAILY_LOSS_LIMIT'
  | 'INSUFFICIENT_CASH'
  | 'INSUFFICIENT_POSITION'
  | 'INVALID_SYMBOL';
export type RiskDecision = { approved: true } | { approved: false; code: RiskCode; reason: string };
export interface AgentDecision {
  id: string;
  signal: Signal;
  status: 'HOLD' | 'EXECUTED' | 'REJECTED';
  tradeId: string | null;
  rejectionCode: RiskCode | null;
  rejectionReason: string | null;
  createdAt: string;
}
export interface PriceQuote {
  price: Amount;
  asOf: string;
  source: string;
}
export type PriceBook = Partial<Record<TradingSymbol, PriceQuote>>;
export interface TradingSnapshot {
  portfolio: Portfolio;
  positions: Position[];
  prices: PriceBook;
  tradesToday: Trade[];
  recentTrades: Trade[];
}
