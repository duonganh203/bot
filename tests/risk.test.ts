import { describe, expect, it } from 'vitest';
import { DailyRiskService, utcDayRange } from '../src/risk/daily-risk.js';
import { RiskEngine } from '../src/risk/risk-engine.js';
import { decimal } from '../src/shared/decimal.js';
import { openDatabase } from '../src/db/client.js';
import { SqliteTradingRepository } from '../src/repositories/sqlite/trading-repository.js';
import type { OrderSignal, Trade } from '../src/domain/trading/types.js';
import { parsedOrder, makeApp, order } from './helpers.js';
import type { ContextResponse, SignalResponse } from './helpers.js';

const now = new Date('2026-09-09T12:00:00.000Z');
const lossTrade = (realizedPnl: string, createdAt: string): Trade => ({
  id: 'test', symbol: 'BTCUSDT', side: 'SELL', quantity: decimal('0.00001'), price: decimal(100000),
  grossUsd: decimal(1), feeUsd: decimal('0.001'), netUsd: decimal('0.999'), realizedPnl: decimal(realizedPnl), createdAt,
});

describe('hard risk rules', () => {
  it('requires enough cash for notional AND fees; allows exact cash', () => {
    const db = openDatabase(':memory:', now);
    try {
      const snapshot = new SqliteTradingRepository(db).snapshot(now);
      const signal = parsedOrder() as OrderSignal;
      const engine = new RiskEngine();
      snapshot.portfolio.cash = decimal(5);
      expect(engine.evaluate(signal, snapshot, new Map(), now)).toMatchObject({ approved: false, code: 'INSUFFICIENT_CASH' });
      snapshot.portfolio.cash = decimal('5.005');
      expect(engine.evaluate(signal, snapshot, new Map(), now)).toEqual({ approved: true });
    } finally { db.close(); }
  });

  it('blocks both BUY and SELL at exactly -$3, permits -$2.999999', () => {
    const db = openDatabase(':memory:', now);
    try {
      const snapshot = new SqliteTradingRepository(db).snapshot(now);
      const engine = new RiskEngine();
      snapshot.tradesToday = [lossTrade('-3', now.toISOString())];
      for (const action of ['BUY', 'SELL']) {
        expect(engine.evaluate(parsedOrder({ action }) as OrderSignal, snapshot, new Map(), now))
          .toMatchObject({ approved: false, code: 'DAILY_LOSS_LIMIT' });
      }
      snapshot.tradesToday = [lossTrade('-2.999999', now.toISOString())];
      expect(engine.evaluate(parsedOrder() as OrderSignal, snapshot, new Map(), now)).toEqual({ approved: true });
    } finally { db.close(); }
  });

  it('uses current UTC day with inclusive start and exclusive next midnight', () => {
    const service = new DailyRiskService();
    const result = service.compute([
      lossTrade('-100', '2026-09-08T23:59:59.999Z'),
      lossTrade('-2', '2026-09-09T00:00:00.000Z'),
      lossTrade('-1', '2026-09-09T23:59:59.999Z'),
      lossTrade('-100', '2026-09-10T00:00:00.000Z'),
    ], now);
    expect(result.realizedPnlToday.toString()).toBe('-3');
    expect(result.blocked).toBe(true);
    expect(result.remainingDailyLossBudgetUsd.toString()).toBe('0');
    expect(utcDayRange(new Date('2026-09-10T01:00:00+07:00'))).toEqual({ start: '2026-09-09T00:00:00.000Z', end: '2026-09-10T00:00:00.000Z' });
  });

  it('uses net daily PnL (profits offset losses), not sum of losing trades', () => {
    const result = new DailyRiskService().compute([
      lossTrade('-4', now.toISOString()), lossTrade('2', now.toISOString()),
    ], now);
    expect(result.realizedPnlToday.toString()).toBe('-2');
    expect(result.remainingDailyLossBudgetUsd.toString()).toBe('1');
    expect(result.blocked).toBe(false);
  });

  it('persists a loss breach, allows HOLD, and unlocks at the next UTC day', async () => {
    let current = new Date('2026-09-09T23:59:59.000Z');
    const app = makeApp({ clock: () => current });
    const submit = (changes: Record<string, unknown>) => app.inject({ method: 'POST', url: '/api/signals', payload: order(changes) });
    try {
      await submit({});
      expect((await submit({ action: 'SELL', price: 20000, amountUsd: 1 })).json<SignalResponse>().trade.realizedPnl).toBe(-4.006);
      for (const action of ['BUY', 'SELL']) {
        expect((await submit({ action })).json<SignalResponse>().risk.code).toBe('DAILY_LOSS_LIMIT');
      }
      expect((await submit({ action: 'HOLD' })).statusCode).toBe(200);
      expect((await app.inject('/api/context')).json<ContextResponse>().risk)
        .toMatchObject({ dailyLossLimitReached: true, remainingDailyLossBudgetUsd: 0 });
      current = new Date('2026-09-10T00:00:00.000Z');
      expect((await app.inject('/api/context')).json<ContextResponse>().risk.remainingDailyLossBudgetUsd).toBe(3);
      expect((await submit({})).statusCode).toBe(200);
    } finally { await app.close(); }
  });
});
