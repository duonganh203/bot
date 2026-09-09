import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { makeApp, order } from './helpers.js';
import type { ContextResponse, DecisionsResponse, SignalResponse } from './helpers.js';
import { PaperTradeExecutor } from '../src/execution/paper-executor.js';
import type { TradeExecutor } from '../src/execution/trade-executor.js';

describe('SQLite durability and atomicity', () => {
  let directory: string;
  let databasePath: string;
  beforeEach(() => {
    directory = mkdtempSync(join(tmpdir(), 'paper-trader-test-'));
    databasePath = join(directory, 'portfolio.sqlite');
  });
  afterEach(() => { rmSync(directory, { recursive: true, force: true }); });

  it('migrations and bootstrap preserve state after closing and reopening the service', async () => {
    const first = makeApp({ databasePath });
    let previous: ContextResponse;
    try {
      await first.inject({ method: 'POST', url: '/api/signals', payload: order() });
      previous = (await first.inject('/api/context')).json<ContextResponse>();
    } finally { await first.close(); }
    const second = makeApp({ databasePath });
    try {
      expect((await second.inject('/api/context')).json<ContextResponse>()).toEqual(previous);
      expect((await second.inject('/api/decisions')).json<DecisionsResponse>().decisions).toHaveLength(1);
    } finally { await second.close(); }
    const db = new Database(databasePath, { readonly: true });
    try {
      expect(db.pragma('integrity_check', { simple: true })).toBe('ok');
      expect(db.prepare('SELECT cash, typeof(cash) AS storage FROM portfolio').get()).toEqual({ cash: '44.995', storage: 'text' });
      expect(db.prepare('SELECT quantity FROM positions').get()).toEqual({ quantity: '0.00005' });
      expect(db.prepare('SELECT COUNT(*) AS count FROM __drizzle_migrations').get()).toEqual({ count: 1 });
      expect(db.pragma('foreign_key_check')).toEqual([]);
    } finally { db.close(); }
  });

  it('rolls back portfolio, position, trade, and quote if decision insert fails', async () => {
    const app = makeApp({ databasePath });
    const inspector = new Database(databasePath);
    try {
      inspector.exec("CREATE TRIGGER fail_decision BEFORE INSERT ON agent_decisions BEGIN SELECT RAISE(ABORT, 'injected failure'); END");
      const response = await app.inject({ method: 'POST', url: '/api/signals', payload: order() });
      expect(response.statusCode).toBe(500);
      expect(response.body).not.toContain('injected failure');
      const context = (await app.inject('/api/context')).json<ContextResponse>();
      expect(context.portfolio.cash).toBe(50);
      expect(context.portfolio.version).toBe(0);
      expect(context.positions).toHaveLength(0);
      expect(context.recentTrades).toHaveLength(0);
      expect(inspector.prepare('SELECT COUNT(*) AS count FROM market_prices').get()).toEqual({ count: 0 });
      inspector.exec('DROP TRIGGER fail_decision');
      expect((await app.inject({ method: 'POST', url: '/api/signals', payload: order() })).statusCode).toBe(200);
    } finally { inspector.close(); await app.close(); }
  });

  it('rejects stale portfolio commits without partial writes', async () => {
    const executor: TradeExecutor = {
      execute: async (signal) => {
        const writer = new Database(databasePath);
        try { writer.exec('UPDATE portfolio SET version = version + 1'); } finally { writer.close(); }
        return new PaperTradeExecutor().execute(signal);
      },
    };
    const app = makeApp({ databasePath, executor });
    try {
      const response = await app.inject({ method: 'POST', url: '/api/signals', payload: order() });
      expect(response.statusCode).toBe(409);
      const context = (await app.inject('/api/context')).json<ContextResponse>();
      expect(context.portfolio.cash).toBe(50);
      expect(context.positions).toHaveLength(0);
      expect(context.recentTrades).toHaveLength(0);
    } finally { await app.close(); }
  });

  it('risk rejection never calls executor and executor failure leaves no financial state', async () => {
    let calls = 0;
    const executor: TradeExecutor = {
      execute: () => { calls++; return Promise.reject(new Error('executor unavailable')); },
    };
    const app = makeApp({ databasePath, executor });
    try {
      expect((await app.inject({ method: 'POST', url: '/api/signals', payload: order({ amountUsd: 6 }) })).statusCode).toBe(422);
      expect(calls).toBe(0);
      expect((await app.inject({ method: 'POST', url: '/api/signals', payload: order() })).statusCode).toBe(500);
      expect(calls).toBe(1);
      expect((await app.inject('/api/context')).json<ContextResponse>().portfolio.cash).toBe(50);
      expect((await app.inject('/api/context')).json<ContextResponse>().recentTrades).toHaveLength(0);
    } finally { await app.close(); }
  });

  it('insufficient cash is audited and cannot go negative', async () => {
    const app = makeApp({ databasePath });
    const inspector = new Database(databasePath);
    try {
      inspector.exec("UPDATE portfolio SET cash = '5'");
      const response = await app.inject({ method: 'POST', url: '/api/signals', payload: order() });
      expect(response.json<SignalResponse>().risk.code).toBe('INSUFFICIENT_CASH');
      expect((await app.inject('/api/decisions')).json<DecisionsResponse>().decisions[0]?.rejectionCode).toBe('INSUFFICIENT_CASH');
      expect(() => inspector.exec("UPDATE portfolio SET cash = '-1'")).toThrow();
      expect(inspector.prepare('SELECT cash FROM portfolio').get()).toEqual({ cash: '5' });
    } finally { inspector.close(); await app.close(); }
  });

  it('db:reset atomically clears paper history and restores $50', async () => {
    const first = makeApp({ databasePath });
    try { await first.inject({ method: 'POST', url: '/api/signals', payload: order() }); } finally { await first.close(); }
    execFileSync(process.execPath, ['--import', 'tsx', 'scripts/reset.ts'], {
      cwd: process.cwd(), env: { ...process.env, DATABASE_PATH: databasePath, HOST: '127.0.0.1' },
      windowsHide: true,
    });
    const second = makeApp({ databasePath });
    try {
      const context = (await second.inject('/api/context')).json<ContextResponse>();
      expect(context.portfolio).toMatchObject({ cash: 50, totalFees: 0, realizedPnl: 0 });
      expect(context.positions).toHaveLength(0);
      expect(context.recentTrades).toHaveLength(0);
      expect((await second.inject('/api/decisions')).json<DecisionsResponse>().decisions).toHaveLength(0);
    } finally { await second.close(); }
  });
});
