import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { makeApp, metadata, order } from './helpers.js';
import type { ContextResponse, DecisionsResponse, SignalResponse, TradeResponse } from './helpers.js';

describe('paper trading through API and SQLite', () => {
  let app: ReturnType<typeof makeApp>;
  beforeEach(() => { app = makeApp(); });
  afterEach(async () => { await app.close(); });
  const submit = (overrides: Record<string, unknown> = {}) => app.inject({ method: 'POST', url: '/api/signals', payload: order(overrides) });
  const context = async (query = '') => (await app.inject(`/api/context${query}`)).json<ContextResponse>();

  it('boots once with $50 and healthy empty context', async () => {
    expect((await app.inject('/health')).json<unknown>()).toEqual({ status: 'ok' });
    const result = await context();
    expect(result.portfolio).toMatchObject({ initialCapital: 50, cash: 50, equity: 50, totalFees: 0, realizedPnl: 0 });
    expect(result.positions).toEqual([]);
    expect(result.risk.remainingDailyLossBudgetUsd).toBe(3);
  });

  it('BUY $5: quantity, fee, debit, average entry, and net equity reconcile', async () => {
    const response = await submit();
    expect(response.statusCode).toBe(200);
    const body = response.json<SignalResponse>();
    expect(body.trade).toMatchObject({ quantity: 0.00005, feeUsd: 0.005, grossUsd: 5, netUsd: 5.005, realizedPnl: 0 });
    expect(body.portfolio).toMatchObject({ cash: 44.995, totalFees: 0.005, equity: 49.995, unrealizedPnl: -0.005 });
    expect((await context()).positions[0]).toMatchObject({ averageEntryPrice: 100000, quantity: 0.00005 });
  });

  it('multiple BUY uses quantity-weighted cost basis', async () => {
    await submit();
    await submit({ price: 200000 });
    const result = await context();
    expect(result.portfolio.cash).toBe(39.99);
    expect(result.positions[0]?.quantity).toBe(0.000075);
    expect(result.positions[0]?.averageEntryPrice).toBeCloseTo(400000 / 3, 8);
    expect(result.portfolio.equity - 50).toBeCloseTo(result.portfolio.realizedPnl + result.portfolio.unrealizedPnl, 12);
  });

  it('SELL profit releases proportional cost and both entry/exit fees', async () => {
    await submit();
    const result = (await submit({ action: 'SELL', amountUsd: 2, price: 200000 })).json<SignalResponse>();
    expect(result.trade).toMatchObject({ quantity: 0.00001, grossUsd: 2, feeUsd: 0.002, netUsd: 1.998, realizedPnl: 0.997 });
    expect(result.portfolio.cash).toBe(46.993);
    expect((await context()).positions[0]).toMatchObject({ quantity: 0.00004, averageEntryPrice: 100000 });
  });

  it('SELL loss is net of allocated fees', async () => {
    await submit();
    const result = (await submit({ action: 'SELL', amountUsd: 1, price: 50000 })).json<SignalResponse>();
    expect(result.trade.realizedPnl).toBe(-1.003);
    expect(result.portfolio.cash).toBe(45.994);
    expect((await context()).positions[0]?.quantity).toBe(0.00003);
  });

  it('full liquidation removes position and a later BUY starts a fresh basis', async () => {
    await submit();
    await submit({ action: 'SELL' });
    const result = await context();
    expect(result.positions).toHaveLength(0);
    expect(result.portfolio).toMatchObject({ cash: 49.99, realizedPnl: -0.01, totalFees: 0.01, unrealizedPnl: 0 });
    await submit({ price: 50000 });
    expect((await context()).positions[0]?.averageEntryPrice).toBe(50000);
  });

  it('HOLD logs a decision without changing portfolio, trades, or prices', async () => {
    await submit();
    const before = await context();
    const response = await submit({ action: 'HOLD', price: 1 });
    expect(response.json<SignalResponse>().status).toBe('held');
    expect(await context()).toEqual(before);
    const decisions = (await app.inject('/api/decisions')).json<DecisionsResponse>().decisions;
    expect(decisions[0]).toMatchObject({ action: 'HOLD', status: 'HOLD', tradeId: null });
    const minimal = await app.inject({ method: 'POST', url: '/api/signals', payload: { action: 'HOLD', ...metadata } });
    expect(minimal.statusCode).toBe(200);
  });

  it('rejects oversized orders and persists audit without mutation', async () => {
    const response = await submit({ amountUsd: '5.000001' });
    expect(response.statusCode).toBe(422);
    expect(response.json<SignalResponse>().risk.code).toBe('ORDER_TOO_LARGE');
    expect((await context()).portfolio.cash).toBe(50);
    expect((await context()).recentTrades).toHaveLength(0);
    expect((await app.inject('/api/decisions')).json<DecisionsResponse>().decisions[0])
      .toMatchObject({ status: 'REJECTED', rejectionCode: 'ORDER_TOO_LARGE' });
  });

  it('rejects unsupported symbols with an auditable risk code', async () => {
    expect((await submit({ symbol: 'DOGEUSDT' })).json<SignalResponse>().risk.code).toBe('INVALID_SYMBOL');
    expect((await app.inject('/api/decisions')).json<DecisionsResponse>().decisions).toHaveLength(1);
  });

  it('rejects SELL without holdings and overselling an existing position', async () => {
    expect((await submit({ action: 'SELL' })).json<SignalResponse>().risk.code).toBe('INSUFFICIENT_POSITION');
    await submit({ amountUsd: 1 });
    expect((await submit({ action: 'SELL', amountUsd: 2 })).json<SignalResponse>().risk.code).toBe('INSUFFICIENT_POSITION');
    expect((await context()).positions[0]?.quantity).toBe(0.00001);
  });

  it('allows exactly $20 exposure then rejects increases, including across symbols', async () => {
    for (let i = 0; i < 4; i++) expect((await submit()).statusCode).toBe(200);
    expect((await context()).portfolio.positionsValue).toBe(20);
    expect((await submit({ symbol: 'ETHUSDT', price: 2000, amountUsd: 0.000001 })).json<SignalResponse>().risk.code).toBe('MAX_EXPOSURE');
  });

  it('marks existing holdings at order price; allows SELL above exposure cap', async () => {
    for (let i = 0; i < 3; i++) await submit();
    expect((await submit({ price: 200000 })).json<SignalResponse>().risk.code).toBe('MAX_EXPOSURE');
    expect((await submit({ action: 'SELL', price: 200000 })).statusCode).toBe(200);
  });

  it('context query marks are read-only; other symbols use persisted prices', async () => {
    await submit();
    await submit({ symbol: 'ETHUSDT', price: 2000 });
    const before = await context();
    const marked = await context('?BTCUSDT=110000&ETHUSDT=2400');
    expect(marked.portfolio.positionsValue).toBe(11.5);
    expect(marked.portfolio.equity).toBe(51.49);
    expect(marked.portfolio.equity - 50).toBeCloseTo(marked.portfolio.realizedPnl + marked.portfolio.unrealizedPnl, 12);
    expect(await context()).toEqual(before);
  });

  it('trade and decision history is newest first, including identical timestamps', async () => {
    await submit();
    await submit({ symbol: 'ETHUSDT', price: 2000 });
    await submit({ action: 'HOLD' });
    const trades = (await app.inject('/api/trades?limit=1')).json<{ trades: TradeResponse[] }>().trades;
    expect(trades).toHaveLength(1);
    expect(trades[0]?.symbol).toBe('ETHUSDT');
    const filtered = (await app.inject('/api/trades?symbol=BTCUSDT&limit=2')).json<{ trades: TradeResponse[] }>().trades;
    expect(filtered).toHaveLength(1);
    expect(filtered[0]?.symbol).toBe('BTCUSDT');
    expect((await app.inject('/api/decisions?limit=1')).json<DecisionsResponse>().decisions[0]?.action).toBe('HOLD');
  });

  it('serializes concurrent requests so only $20 can execute', async () => {
    const responses = await Promise.all(Array.from({ length: 30 }, () => submit({ amountUsd: 1 })));
    expect(responses.filter((response) => response.statusCode === 200)).toHaveLength(20);
    expect(responses.filter((response) => response.statusCode === 422)).toHaveLength(10);
    const result = await context();
    expect(result.portfolio).toMatchObject({ cash: 29.98, positionsValue: 20, totalFees: 0.02 });
    expect((await app.inject('/api/decisions')).json<DecisionsResponse>().decisions).toHaveLength(30);
  });

  it('supports exact decimal input strings and repeating quantities without float drift', async () => {
    const bought = (await submit({ amountUsd: '5', price: '112400' })).json<SignalResponse>();
    expect(bought.portfolio.cash).toBe(44.995);
    await submit({ action: 'SELL', amountUsd: '5', price: '112400' });
    expect((await context()).positions).toHaveLength(0);
    expect((await context()).portfolio.cash).toBe(49.99);
  });

  it.each([
    { amountUsd: 0 }, { amountUsd: -1 }, { price: 0 }, { price: -2 },
    { amountUsd: 0.0000001 }, { price: 'Infinity' }, { amountUsd: 'NaN' },
    { confidence: 1.1 }, { confidence: -0.1 }, { action: 'SHORT' },
    { leverage: 10 }, { riskLevel: 'UNSAFE' }, { rationale: '' }, { source: '' },
    { price: 1000000001 }, { amountUsd: '1e9999' },
  ])('rejects malformed signals: %j', async (changes) => {
    expect((await submit(changes)).statusCode).toBe(400);
    expect((await context()).portfolio.cash).toBe(50);
    expect((await app.inject('/api/decisions')).json<DecisionsResponse>().decisions).toHaveLength(0);
  });

  it.each(['/api/trades?limit=0', '/api/trades?limit=201', '/api/trades?limit=1.5',
    '/api/trades?symbol=DOGEUSDT', '/api/context?BTCUSDT=0', '/api/context?ETHUSDT=no',
    '/api/context?leverage=2', '/api/decisions?limit=no'])('validates queries: %s', async (url) => {
    expect((await app.inject(url)).statusCode).toBe(400);
  });

  it('returns safe errors for malformed JSON and oversized bodies', async () => {
    const malformed = await app.inject({ method: 'POST', url: '/api/signals', payload: '{', headers: { 'content-type': 'application/json' } });
    expect(malformed.statusCode).toBe(400);
    expect((await submit({ rationale: 'x'.repeat(20_000) })).statusCode).toBe(413);
  });
});
