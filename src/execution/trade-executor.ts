import type { OrderSignal, TradingSymbol } from '../domain/trading/types.js';
import type { Amount } from '../shared/decimal.js';

export interface ExecutionResult {
  symbol: TradingSymbol;
  side: 'BUY' | 'SELL';
  quantity: Amount;
  price: Amount;
  grossUsd: Amount;
  feeUsd: Amount;
}

export interface TradeExecutor {
  execute(signal: OrderSignal & { symbol: TradingSymbol }): Promise<ExecutionResult>;
}
