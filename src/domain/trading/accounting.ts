import type { Portfolio, Position, Trade } from './types.js';
import type { ExecutionResult } from '../../execution/trade-executor.js';
import { decimal } from '../../shared/decimal.js';

export function applyFill(
  portfolio: Portfolio,
  existing: Position | undefined,
  fill: ExecutionResult,
  tradeId: string,
  now: string,
): { portfolio: Portfolio; position: Position; trade: Trade } {
  const previous: Position = existing ?? {
    symbol: fill.symbol, quantity: decimal(0), costBasisUsd: decimal(0), entryFeesUsd: decimal(0),
    averageEntryPrice: decimal(0), createdAt: now, updatedAt: now,
  };
  let quantity;
  let costBasisUsd;
  let entryFeesUsd;
  let realizedPnl = decimal(0);
  const netUsd = fill.side === 'BUY' ? fill.grossUsd.plus(fill.feeUsd) : fill.grossUsd.minus(fill.feeUsd);
  if (fill.side === 'BUY') {
    quantity = previous.quantity.plus(fill.quantity);
    costBasisUsd = previous.costBasisUsd.plus(fill.grossUsd);
    entryFeesUsd = previous.entryFeesUsd.plus(fill.feeUsd);
  } else {
    if (previous.quantity.lt(fill.quantity) || previous.quantity.isZero()) {
      throw new Error('Accounting invariant: insufficient position');
    }
    const ratio = fill.quantity.div(previous.quantity);
    const releasedCost = previous.costBasisUsd.mul(ratio);
    const releasedFees = previous.entryFeesUsd.mul(ratio);
    quantity = previous.quantity.minus(fill.quantity);
    costBasisUsd = previous.costBasisUsd.minus(releasedCost);
    entryFeesUsd = previous.entryFeesUsd.minus(releasedFees);
    realizedPnl = netUsd.minus(releasedCost).minus(releasedFees);
  }
  const cash = fill.side === 'BUY' ? portfolio.cash.minus(netUsd) : portfolio.cash.plus(netUsd);
  if (cash.isNegative() || quantity.isNegative() || !fill.quantity.isPositive()) {
    throw new Error('Accounting invariant: negative balance or zero fill');
  }
  return {
    portfolio: {
      ...portfolio, cash, realizedPnl: portfolio.realizedPnl.plus(realizedPnl),
      totalFees: portfolio.totalFees.plus(fill.feeUsd), version: portfolio.version + 1, updatedAt: now,
    },
    position: {
      ...previous, quantity, costBasisUsd, entryFeesUsd,
      averageEntryPrice: quantity.isZero() ? decimal(0) : costBasisUsd.div(quantity), updatedAt: now,
    },
    trade: { ...fill, id: tradeId, netUsd, realizedPnl, createdAt: now },
  };
}
