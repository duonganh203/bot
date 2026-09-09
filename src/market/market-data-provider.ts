import type { PriceBook, TradingSymbol } from '../domain/trading/types.js';
import type { Amount } from '../shared/decimal.js';
import { AppError } from '../shared/errors.js';

export interface MarketDataProvider {
  getPrice(symbol: TradingSymbol): Promise<Amount>;
}

// Replace this adapter when adding an external market-data feed.
export class ManualMarketDataProvider implements MarketDataProvider {
  constructor(private readonly prices: PriceBook) {}

  getPrice(symbol: TradingSymbol): Promise<Amount> {
    const quote = this.prices[symbol];
    if (!quote) {
      return Promise.reject(new AppError('MISSING_MARKET_PRICE', `Missing price for ${symbol}.`, 422));
    }
    return Promise.resolve(quote.price);
  }
}
