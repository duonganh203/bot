import type { Portfolio, Position, TradingSymbol } from './types.js';
import { decimal } from '../../shared/decimal.js';
import type { Amount } from '../../shared/decimal.js';
import type { MarketDataProvider } from '../../market/market-data-provider.js';

export type Marks = ReadonlyMap<TradingSymbol, Amount>;

export async function loadMarks(positions: readonly Position[], provider: MarketDataProvider): Promise<Marks> {
  return new Map(await Promise.all(positions.map(async (position) =>
    [position.symbol, await provider.getPrice(position.symbol)] as const,
  )));
}

export function markedPositions(positions: readonly Position[], marks: Marks) {
  return positions.map((position) => {
    const currentPrice = marks.get(position.symbol);
    if (!currentPrice) throw new Error(`Missing mark for ${position.symbol}`);
    const marketValue = position.quantity.mul(currentPrice);
    return {
      ...position,
      currentPrice,
      marketValue,
      unrealizedPnl: marketValue.minus(position.costBasisUsd).minus(position.entryFeesUsd),
    };
  });
}

export function valuePortfolio(portfolio: Portfolio, positions: readonly Position[], marks: Marks) {
  const valued = markedPositions(positions, marks);
  const positionsValue = valued.reduce((sum, position) => sum.plus(position.marketValue), decimal(0));
  const unrealizedPnl = valued.reduce((sum, position) => sum.plus(position.unrealizedPnl), decimal(0));
  return { ...portfolio, positionsValue, equity: portfolio.cash.plus(positionsValue), unrealizedPnl };
}
