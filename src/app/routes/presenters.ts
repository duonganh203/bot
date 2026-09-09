import { Decimal } from 'decimal.js';
import { asNumber } from '../../shared/decimal.js';

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

// Explicit boundary conversion: Decimal.toJSON() normally returns strings.
export function present(value: unknown): JsonValue {
  if (Decimal.isDecimal(value)) return asNumber(value);
  if (value === null || typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number') return value;
  if (Array.isArray(value)) return value.map((item: unknown) => present(item));
  if (typeof value === 'object') {
    const result: Record<string, JsonValue> = {};
    for (const [key, child] of Object.entries(value)) {
      if (child !== undefined && key !== 'sequence') result[key] = present(child);
    }
    return result;
  }
  throw new Error('Unsupported response value');
}
