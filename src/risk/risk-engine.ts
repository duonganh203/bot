import { isTradingSymbol, SYMBOLS } from '../domain/trading/types.js';
import type { OrderSignal, RiskDecision, TradingSnapshot } from '../domain/trading/types.js';
import { markedPositions } from '../domain/trading/valuation.js';
import type { Marks } from '../domain/trading/valuation.js';
import { decimal, quantityFor } from '../shared/decimal.js';
import { DailyRiskService } from './daily-risk.js';
import { equityFloor, LIMITS } from './limits.js';
import type { RiskPolicy } from './limits.js';

export class RiskEngine {
  constructor(private readonly dailyRisk = new DailyRiskService(), readonly policy: RiskPolicy = 'legacy-v1') {}

  evaluate(signal: OrderSignal, snapshot: TradingSnapshot, marks: Marks, now: Date): RiskDecision {
    if (!isTradingSymbol(signal.symbol)) {
      return { approved: false, code: 'INVALID_SYMBOL', reason: `Allowed symbols: ${SYMBOLS.join(', ')}.` };
    }
    if (signal.amountUsd.gt(LIMITS.maxOrderUsd)) {
      return { approved: false, code: 'ORDER_TOO_LARGE', reason: 'Order exceeds max order size of $5.' };
    }
    if (this.dailyRisk.compute(snapshot.tradesToday, now).blocked &&
        (this.policy === 'legacy-v1' || signal.action === 'BUY')) {
      return { approved: false, code: 'DAILY_LOSS_LIMIT', reason: 'UTC daily realized loss has reached $3.' };
    }
    if (signal.action === 'SELL') {
      const position = snapshot.positions.find((item) => item.symbol === signal.symbol);
      if (!position || quantityFor(signal.amountUsd, signal.price).gt(position.quantity)) {
        return { approved: false, code: 'INSUFFICIENT_POSITION', reason: 'SELL quantity exceeds the spot position.' };
      }
    } else {
      const debit = signal.amountUsd.mul(decimal(1).plus(LIMITS.feeRate));
      if (snapshot.portfolio.cash.lt(debit)) {
        return { approved: false, code: 'INSUFFICIENT_CASH', reason: 'Cash must cover order notional plus the 0.1% fee.' };
      }
      const exposure = markedPositions(snapshot.positions, marks)
        .reduce((sum, position) => sum.plus(position.marketValue), decimal(0));
      if (this.policy === 'reduce-only-v2' && snapshot.portfolio.cash.plus(exposure)
        .minus(signal.amountUsd.mul(LIMITS.feeRate)).lte(equityFloor(snapshot.portfolio.initialCapital))) {
        return { approved: false, code: 'EQUITY_LOSS_LIMIT', reason: 'BUY would leave marked equity at or below initial capital minus $3, including entry fee.' };
      }
      if (exposure.plus(signal.amountUsd).gt(LIMITS.maxExposureUsd)) {
        return { approved: false, code: 'MAX_EXPOSURE', reason: 'BUY would exceed $20 marked portfolio exposure.' };
      }
    }
    return { approved: true };
  }
}
