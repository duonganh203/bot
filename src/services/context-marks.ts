import { z } from 'zod';
import type { ContextSnapshot, OrderSignal, PriceBook, TradingSnapshot } from '../domain/trading/types.js';
import { isTradingSymbol, SYMBOLS } from '../domain/trading/types.js';
import { decimal } from '../shared/decimal.js';
import { AppError } from '../shared/errors.js';

const quotePrice = z.union([
  z.number().positive(),
  z.string().max(32).regex(/^\d+(?:\.\d{1,6})?$/).refine((value) => decimal(value).gt(0)),
]);
const quote = z.object({ price: quotePrice, asOf: z.string() });
const shape = z.object({ marketData: z.object({ quotes: z.partialRecord(z.enum(SYMBOLS), quote) }) });

// V2 must not value the other coin using an hours-old last fill. Use the exact
// persisted decision snapshot and reject stale/version-mismatched submissions.
export function contextMarks(context: ContextSnapshot | undefined, snapshot: TradingSnapshot, signal: OrderSignal, now: Date): PriceBook {
  if (!context) throw new AppError('FRESH_CONTEXT_REQUIRED', 'V2 orders require a context with both current quotes.', 422);
  if (context.portfolioVersion !== snapshot.portfolio.version) {
    throw new AppError('CONTEXT_VERSION_CONFLICT', 'Portfolio changed after the supplied context.', 409);
  }
  const fresh = (asOf: string) => {
    const age = now.getTime() - Date.parse(asOf);
    return Number.isFinite(age) && age >= -30_000 && age < 240_000 && asOf.slice(0, 10) === now.toISOString().slice(0, 10);
  };
  const parsed = shape.safeParse(context.payload);
  if (!fresh(context.createdAt) || !parsed.success) {
    throw new AppError('FRESH_CONTEXT_REQUIRED', 'V2 context must include fresh quotes for both coins.', 422);
  }
  const prices: PriceBook = {};
  // Keep the original BTC/ETH context contract, and require current marks for
  // every held asset plus the asset being traded. Unheld altcoins are optional.
  const required = new Set(['BTCUSDT', 'ETHUSDT', ...snapshot.positions.map((p) => p.symbol)]);
  if (isTradingSymbol(signal.symbol)) required.add(signal.symbol);
  for (const symbol of SYMBOLS.filter((s) => required.has(s))) {
    const value = parsed.data.marketData.quotes[symbol];
    if (!value || !fresh(value.asOf)) throw new AppError('FRESH_CONTEXT_REQUIRED', `Missing or stale context quote: ${symbol}.`, 422);
    prices[symbol] = { price: decimal(value.price), asOf: value.asOf, source: 'decision-context' };
  }
  const mark = prices[signal.symbol as keyof PriceBook];
  if (mark && !mark.price.eq(signal.price)) {
    throw new AppError('CONTEXT_PRICE_MISMATCH', 'Order price differs from the saved decision context.', 422);
  }
  return prices;
}
