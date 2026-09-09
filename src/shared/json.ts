import { Decimal } from 'decimal.js';

export type JsonValue = string | number | boolean | null | JsonValue[] | JsonObject;
export interface JsonObject { [key: string]: JsonValue }

export function toJson(value: unknown, decimals: 'number' | 'string' = 'number'): JsonValue {
  if (Decimal.isDecimal(value)) return decimals === 'number' ? value.toNumber() : value.toFixed();
  if (value === null || typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number') return value;
  if (Array.isArray(value)) return value.map((item: unknown) => toJson(item, decimals));
  if (typeof value === 'object') {
    const result: JsonObject = {};
    for (const [key, child] of Object.entries(value)) {
      if (child !== undefined && key !== 'sequence') result[key] = toJson(child, decimals);
    }
    return result;
  }
  throw new Error('Unsupported JSON value');
}
