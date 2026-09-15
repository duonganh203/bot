import { describe, expect, it } from 'vitest';
import { makeApp, order } from './helpers.js';
import type { ContextResponse, SignalResponse } from './helpers.js';

describe('reduce-only-v2 risk policy', () => {
  it('uses current marks for the other coin and permits reducing sells after daily AND equity breach', async () => {
    let now = new Date('2026-09-15T10:00:00Z');
    const app = makeApp({ riskPolicy: 'reduce-only-v2', clock: () => now });
    const context = async (btc = 100, eth = 100) => (await app.inject(`/api/context?BTCUSDT=${btc}&ETHUSDT=${eth}`)).json<ContextResponse>();
    const submit = async (changes: Record<string, unknown>, btc = 100, eth = 100) => {
      const c = await context(btc, eth);
      return app.inject({ method: 'POST', url: '/api/signals', payload: order({ price: btc, contextId: c.contextId, ...changes }) });
    };
    try {
      expect((await submit({})).statusCode).toBe(200);
      expect((await submit({ symbol: 'ETHUSDT', price: 100 })).statusCode).toBe(200);
      // Old BTC fill is 100, but current BTC mark is 20: equity is $45.99.
      const blocked = await submit({ symbol: 'ETHUSDT', price: 100 }, 20);
      expect(blocked.json<SignalResponse>().risk.code).toBe('EQUITY_LOSS_LIMIT');
      expect((await submit({ action: 'SELL', amountUsd: 1, price: 20 }, 20)).statusCode).toBe(200);
      const c = await context(20);
      expect(c.risk).toMatchObject({ dailyLossLimitReached: true, equityLossLimitReached: true, reducingSellsAllowed: true });
      expect((await submit({ symbol: 'ETHUSDT', action: 'SELL', price: 100 }, 20)).statusCode).toBe(200);
      expect((await submit({})).json<SignalResponse>().risk.code).toBe('DAILY_LOSS_LIMIT');
      expect((await submit({ action: 'SELL' })).json<SignalResponse>().risk.code).toBe('INSUFFICIENT_POSITION');
      now = new Date('2026-09-16T00:00:00Z');
      expect((await context()).risk.dailyLossLimitReached).toBe(false);
      // UTC midnight resets daily realized loss, but not total-equity protection.
      expect((await submit({})).json<SignalResponse>().risk.code).toBe('EQUITY_LOSS_LIMIT');
    } finally { await app.close(); }
  });

  it('requires complete fresh context, matching version and matching fill quote; replay survives staleness', async () => {
    let now = new Date('2026-09-15T10:00:00Z');
    const app = makeApp({ riskPolicy: 'reduce-only-v2', clock: () => now });
    const post = (payload: Record<string, unknown>, key?: string) => app.inject({ method: 'POST', url: '/api/signals', payload,
      ...(key ? { headers: { 'idempotency-key': key } } : {}) });
    try {
      expect((await post(order())).statusCode).toBe(422);
      const incomplete = (await app.inject('/api/context?BTCUSDT=100000')).json<ContextResponse>();
      expect((await post(order({ contextId: incomplete.contextId }))).statusCode).toBe(422);
      const c = (await app.inject('/api/context?BTCUSDT=100000&ETHUSDT=2000')).json<ContextResponse>();
      expect((await post(order({ contextId: c.contextId, price: 99999 }))).statusCode).toBe(422);
      const payload = order({ contextId: c.contextId });
      const first = await post(payload, 'v2-replay');
      expect(first.statusCode).toBe(200);
      expect((await post(order({ contextId: c.contextId, amountUsd: 1 }))).statusCode).toBe(409);
      const stale = (await app.inject('/api/context?BTCUSDT=100000&ETHUSDT=2000')).json<ContextResponse>();
      now = new Date(now.getTime() + 240_000);
      expect((await post(order({ contextId: stale.contextId }))).statusCode).toBe(422);
      const replay = await post(payload, 'v2-replay');
      expect(replay.statusCode).toBe(200);
      expect(replay.json<SignalResponse>().decisionId).toBe(first.json<SignalResponse>().decisionId);
      expect(replay.headers['idempotency-replayed']).toBe('true');
    } finally { await app.close(); }
  });
});
