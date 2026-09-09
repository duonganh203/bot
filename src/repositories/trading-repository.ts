import type {
  AgentDecision, ContextSnapshot, DecisionAudit, Portfolio, Position, PriceQuote,
  SignalReceipt, Trade, TradingSnapshot, TradingSymbol,
} from '../domain/trading/types.js';
import type { JsonValue } from '../shared/json.js';

export interface CommitTrade {
  expectedVersion: number;
  portfolio: Portfolio;
  position: Position;
  trade: Trade;
  decision: AgentDecision;
  quote: PriceQuote;
  audit: DecisionAudit;
}
export interface HistoryFilter {
  limit: number;
  symbol?: TradingSymbol | undefined;
}

// A single repository boundary keeps portfolio/trade/audit writes atomic.
export interface TradingRepository {
  snapshot(now: Date): TradingSnapshot;
  saveDecision(decision: AgentDecision, audit: DecisionAudit): void;
  commitTrade(commit: CommitTrade): void;
  saveContext(context: ContextSnapshot): void;
  getContext(id: string): ContextSnapshot | undefined;
  getReceipt(key: string): SignalReceipt | undefined;
  getDecision(id: string): DecisionRecord | undefined;
  listTrades(filter: HistoryFilter): Trade[];
  listDecisions(limit: number): DecisionRecord[];
}

export interface DecisionRecord {
  id: string;
  action: 'BUY' | 'SELL' | 'HOLD';
  symbol: string | null;
  amountUsd: import('../shared/decimal.js').Amount | null;
  price: import('../shared/decimal.js').Amount | null;
  confidence: number;
  riskLevel: 'LOW' | 'MEDIUM' | 'HIGH';
  rationale: string;
  source: string;
  strategyId: string;
  strategyVersion: string;
  contextId: string | null;
  executionContext: JsonValue;
  status: 'HOLD' | 'EXECUTED' | 'REJECTED';
  tradeId: string | null;
  rejectionCode: string | null;
  rejectionReason: string | null;
  createdAt: string;
}
