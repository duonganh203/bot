import type {
  AgentDecision, Portfolio, Position, PriceQuote, Trade, TradingSnapshot, TradingSymbol,
} from '../domain/trading/types.js';

export interface CommitTrade {
  expectedVersion: number;
  portfolio: Portfolio;
  position: Position;
  trade: Trade;
  decision: AgentDecision;
  quote: PriceQuote;
}
export interface HistoryFilter {
  limit: number;
  symbol?: TradingSymbol | undefined;
}

// A single repository boundary keeps portfolio/trade/audit writes atomic.
export interface TradingRepository {
  snapshot(now: Date): TradingSnapshot;
  saveDecision(decision: AgentDecision): void;
  commitTrade(commit: CommitTrade): void;
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
  status: 'HOLD' | 'EXECUTED' | 'REJECTED';
  tradeId: string | null;
  rejectionCode: string | null;
  rejectionReason: string | null;
  createdAt: string;
}
