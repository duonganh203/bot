import { z } from 'zod';
import { SYMBOLS } from '../../domain/trading/types.js';
import { decimal } from '../../shared/decimal.js';

// Numeric JSON and exact decimal strings are accepted. Money/price input has 6 decimal places.
const decimalInput = z.union([
  z.number(),
  z.string().max(32).regex(/^\d+(?:\.\d{1,6})?$/),
]).transform((value) => decimal(value))
  .refine((value) => value.isFinite() && value.gte('0.000001') && value.lte('1000000000') && value.decimalPlaces() <= 6,
    'Must be between 0.000001 and 1000000000 with at most 6 decimal places');

const metadata = {
  confidence: z.number().min(0).max(1),
  riskLevel: z.enum(['LOW', 'MEDIUM', 'HIGH']),
  rationale: z.string().trim().min(1).max(2000),
  source: z.string().trim().min(1).max(100),
  strategyId: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/).optional(),
  strategyVersion: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/).optional(),
  contextId: z.uuid().optional(),
};
const order = {
  ...metadata,
  symbol: z.string().min(1).max(32),
  amountUsd: decimalInput,
  price: decimalInput,
};

export const signalSchema = z.discriminatedUnion('action', [
  z.strictObject({ action: z.literal('BUY'), ...order }),
  z.strictObject({ action: z.literal('SELL'), ...order }),
  z.strictObject({ action: z.literal('HOLD'), ...metadata,
    symbol: z.string().min(1).max(32).optional(), amountUsd: decimalInput.optional(), price: decimalInput.optional() }),
]).refine((signal) => Boolean(signal.strategyId) === Boolean(signal.strategyVersion),
  'Provide both strategyId and strategyVersion, or omit both');

export const signalHeadersSchema = z.object({
  'idempotency-key': z.string().regex(/^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/).optional(),
});
export const idParamsSchema = z.strictObject({ id: z.uuid() });
export const contextQuerySchema = z.strictObject({
  BTCUSDT: decimalInput.optional(),
  ETHUSDT: decimalInput.optional(),
});
const limit = z.coerce.number().int().min(1).max(200).default(50);
export const tradesQuerySchema = z.strictObject({ limit, symbol: z.enum(SYMBOLS).optional() });
export const decisionsQuerySchema = z.strictObject({ limit });
