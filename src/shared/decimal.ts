import { Decimal } from 'decimal.js';

// Isolated constructor: other packages cannot change our arithmetic settings.
export const D = Decimal.clone({ precision: 48, rounding: Decimal.ROUND_HALF_UP });
export type Amount = Decimal;
export const decimal = (value: Decimal.Value): Amount => new D(value);
export const quantityFor = (usd: Amount, price: Amount): Amount =>
  usd.div(price).toDecimalPlaces(24, D.ROUND_DOWN);

// Numbers are presentation only. All calculations and persisted values are decimal.
export const asNumber = (value: Amount): number => value.toNumber();
