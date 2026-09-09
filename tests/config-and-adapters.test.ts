import { describe, expect, it } from 'vitest';
import { loadEnv } from '../src/config/env.js';
import { ManualMarketDataProvider } from '../src/market/market-data-provider.js';
import { decimal } from '../src/shared/decimal.js';
import { makeApp, order } from './helpers.js';
import type { ContextResponse, SignalResponse } from './helpers.js';

describe('configuration and adapters', () => {
  it('validates environment and allows token-free local or remote binding', () => {
    expect(loadEnv({})).toMatchObject({ HOST: '127.0.0.1', PORT: 3000, DATABASE_PATH: './data/paper-trader.sqlite' });
    expect(() => loadEnv({ PORT: 'invalid' })).toThrow();
    expect(loadEnv({ HOST: '0.0.0.0' })).toMatchObject({ HOST: '0.0.0.0', API_TOKEN: '' });
    expect(loadEnv({ HOST: '0.0.0.0', API_TOKEN: 'x'.repeat(24) }).HOST).toBe('0.0.0.0');
  });

  it('protects API with an optional bearer token and keeps health public', async () => {
    const app = makeApp({ apiToken: 'test-token' });
    try {
      expect((await app.inject('/health')).statusCode).toBe(200);
      expect((await app.inject('/api/context')).statusCode).toBe(401);
      expect((await app.inject({ url: '/api/context', headers: { authorization: 'Bearer wrong' } })).statusCode).toBe(401);
      expect((await app.inject({ url: '/api/context', headers: { authorization: 'Bearer test-token' } })).statusCode).toBe(200);
    } finally { await app.close(); }
  });

  it('manual provider reports missing prices explicitly', async () => {
    await expect(new ManualMarketDataProvider({}).getPrice('BTCUSDT')).rejects.toMatchObject({ code: 'MISSING_MARKET_PRICE' });
  });

  it('injects market data into valuation and exposure checks', async () => {
    const app = makeApp({ marketData: { getPrice: () => Promise.resolve(decimal(500000)) } });
    try {
      await app.inject({ method: 'POST', url: '/api/signals', payload: order() });
      const context = (await app.inject('/api/context')).json<ContextResponse>();
      expect(context.portfolio.positionsValue).toBe(25);
      const response = await app.inject({ method: 'POST', url: '/api/signals', payload: order() });
      expect(response.json<SignalResponse>().risk.code).toBe('MAX_EXPOSURE');
    } finally { await app.close(); }
  });
});
