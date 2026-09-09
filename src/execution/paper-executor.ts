import type { OrderSignal, TradingSymbol } from '../domain/trading/types.js';
import { quantityFor } from '../shared/decimal.js';
import { LIMITS } from '../risk/limits.js';
import type { ExecutionResult, TradeExecutor } from './trade-executor.js';

export class PaperTradeExecutor implements TradeExecutor {
  execute(signal: OrderSignal & { symbol: TradingSymbol }): Promise<ExecutionResult> {
    return Promise.resolve({
      symbol: signal.symbol,
      side: signal.action,
      quantity: quantityFor(signal.amountUsd, signal.price),
      price: signal.price,
      grossUsd: signal.amountUsd,
      feeUsd: signal.amountUsd.mul(LIMITS.feeRate),
    });
  }
}
