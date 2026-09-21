import { expect, it } from 'vitest';
import { makeApp, order } from './helpers.js';

it('preserves exact paper quantities through context, fills and idempotent replay', async () => {
  const app = makeApp({ riskPolicy: 'reduce-only-v2' });
  const headers = { 'x-decimal-format': 'string' };
  type ExactContext = { contextId: string; portfolio: { cash: string; version: number }; positions: { quantity: string }[] };
  type ExactFill = { status: string; trade: { quantity: string; feeUsd: string }; portfolio: { cash: string } };
  try {
    const before = (await app.inject({ url: '/api/context?BTCUSDT=3&ETHUSDT=3', headers })).json<ExactContext>();
    expect(before.portfolio.cash).toBe('50');
    const payload = order({ price: 3, contextId: before.contextId });
    const first = await app.inject({ method: 'POST', url: '/api/signals', headers: { ...headers, 'idempotency-key': 'exact-fill' }, payload });
    expect(first.statusCode).toBe(200);
    const fill = first.json<ExactFill>();
    expect(fill.trade.quantity).toBe('1.666666666666666666666666');
    expect(fill.trade.feeUsd).toBe('0.005');
    const replay = await app.inject({ method: 'POST', url: '/api/signals', headers: { ...headers, 'idempotency-key': 'exact-fill' }, payload });
    expect(replay.json<ExactFill>()).toEqual(fill);
    expect(replay.headers['idempotency-replayed']).toBe('true');
    const held = (await app.inject({ url: '/api/context?BTCUSDT=3&ETHUSDT=3', headers })).json<ExactContext>();
    expect(held.positions[0]?.quantity).toBe(fill.trade.quantity);
    expect(held.portfolio.version).toBe(1);
    const sold = await app.inject({ method: 'POST', url: '/api/signals', headers,
      payload: order({ action: 'SELL', price: 3, contextId: held.contextId }) });
    expect(sold.json<ExactFill>().status).toBe('executed');
    expect(sold.json<ExactFill>().portfolio.cash).toBe('49.99');
    const after = (await app.inject({ url: '/api/context?BTCUSDT=3&ETHUSDT=3', headers })).json<ExactContext>();
    expect(after.positions).toEqual([]);
  } finally { await app.close(); }
});

it('keeps the default numeric presentation for existing clients', async () => {
  const app = makeApp();
  try {
    const context = (await app.inject('/api/context')).json<{ portfolio: { cash: number } }>();
    expect(context.portfolio.cash).toBe(50);
  } finally { await app.close(); }
});
