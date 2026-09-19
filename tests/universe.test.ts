import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import { describe, expect, it } from 'vitest';
import { makeApp, order } from './helpers.js';
import type { ContextResponse, SignalResponse } from './helpers.js';

const prices = '/api/context?BTCUSDT=100&ETHUSDT=100&SOLUSDT=100&BNBUSDT=100&XRPUSDT=100';

describe('five-coin paper universe', () => {
  it.each(['SOLUSDT', 'BNBUSDT', 'XRPUSDT'])('accounts for %s buys, sells and fees with idempotent replay', async (symbol) => {
    const app = makeApp({ riskPolicy: 'reduce-only-v2' });
    try {
      const before = (await app.inject(prices)).json<ContextResponse>();
      const request = { method: 'POST' as const, url: '/api/signals', headers: { 'idempotency-key': 'alt-buy' },
        payload: order({ symbol, price: 100, contextId: before.contextId }) };
      expect((await app.inject(request)).json<SignalResponse>().status).toBe('executed');
      expect((await app.inject(request)).headers['idempotency-replayed']).toBe('true');
      const held = (await app.inject(prices)).json<ContextResponse>();
      expect(held.portfolio.cash).toBe(44.995);
      expect(held.positions[0]).toMatchObject({ symbol, quantity: 0.05 });
      const sold = await app.inject({ method: 'POST', url: '/api/signals',
        payload: order({ action: 'SELL', symbol, price: 100, contextId: held.contextId }) });
      expect(sold.json<SignalResponse>().status).toBe('executed');
      const after = (await app.inject(prices)).json<ContextResponse>();
      expect(after.portfolio).toMatchObject({ cash: 49.99, realizedPnl: -0.01, totalFees: 0.01 });
      expect(after.positions).toHaveLength(0);
      expect((await app.inject(`/api/trades?symbol=${symbol}`)).json<{ trades: unknown[] }>().trades).toHaveLength(2);
    } finally { await app.close(); }
  });

  it('counts all five coins against the same $20 exposure cap', async () => {
    const app = makeApp({ riskPolicy: 'reduce-only-v2' });
    try {
      for (const symbol of ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT']) {
        const c = (await app.inject(prices)).json<ContextResponse>();
        const response = await app.inject({ method: 'POST', url: '/api/signals',
          payload: order({ symbol, price: 100, contextId: c.contextId }) });
        if (symbol === 'XRPUSDT') expect(response.json<SignalResponse>().risk.code).toBe('MAX_EXPOSURE');
        else expect(response.statusCode).toBe(200);
      }
      expect((await app.inject(prices)).json<ContextResponse>().portfolio.positionsValue).toBe(20);
    } finally { await app.close(); }
  });

  it('requires fresh altcoin marks and permits its reducing sale after an equity loss', async () => {
    let now = new Date('2026-09-19T10:00:00Z');
    const app = makeApp({ riskPolicy: 'reduce-only-v2', clock: () => now });
    const submit = (contextId: string, changes = {}) => app.inject({ method: 'POST', url: '/api/signals',
      payload: order({ price: 100, contextId, ...changes }) });
    try {
      const missing = (await app.inject('/api/context?BTCUSDT=100&ETHUSDT=100')).json<ContextResponse>();
      expect((await submit(missing.contextId, { symbol: 'SOLUSDT' })).statusCode).toBe(422);
      const initial = (await app.inject(prices)).json<ContextResponse>();
      expect((await submit(initial.contextId, { symbol: 'SOLUSDT' })).statusCode).toBe(200);
      now = new Date(now.getTime() + 241_000);
      const staleSol = (await app.inject('/api/context?BTCUSDT=100&ETHUSDT=100')).json<ContextResponse>();
      expect((await submit(staleSol.contextId)).statusCode).toBe(422);
      const loss = (await app.inject(prices.replace('SOLUSDT=100', 'SOLUSDT=20'))).json<ContextResponse>();
      expect((await submit(loss.contextId)).json<SignalResponse>().risk.code).toBe('EQUITY_LOSS_LIMIT');
      expect((await submit(loss.contextId, { action: 'SELL', symbol: 'SOLUSDT', price: 20, amountUsd: 1 })).statusCode).toBe(200);
    } finally { await app.close(); }
  });

  it('rebuilds a populated two-coin database without altering trades, contexts, or receipts', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'universe-migration-'));
    const sourcePath = join(directory, 'source.sqlite');
    const legacyPath = join(directory, 'legacy.sqlite');
    const tables = ['portfolio', 'positions', 'market_prices', 'trades', 'context_snapshots', 'agent_decisions', 'signal_receipts'];
    try {
      const source = makeApp({ databasePath: sourcePath });
      const c = (await source.inject(prices)).json<ContextResponse>();
      await source.inject({ method: 'POST', url: '/api/signals', headers: { 'idempotency-key': 'preserve' }, payload: order({ contextId: c.contextId }) });
      await source.close();
      const sourceDb = new Database(sourcePath);
      const original = Object.fromEntries(tables.map((table) => [table, sourceDb.prepare(`SELECT * FROM ${table}`).all() as Record<string, unknown>[]]));
      sourceDb.close();
      const migrations = join(directory, 'migrations');
      mkdirSync(join(migrations, 'meta'), { recursive: true });
      for (const file of ['0000_whole_mindworm.sql', '0001_lazy_sue_storm.sql']) copyFileSync(`drizzle/${file}`, join(migrations, file));
      const journal = JSON.parse(readFileSync('drizzle/meta/_journal.json', 'utf8')) as { entries: unknown[] };
      writeFileSync(join(migrations, 'meta/_journal.json'), JSON.stringify({ ...journal, entries: journal.entries.slice(0, 2) }));
      const legacy = new Database(legacyPath);
      migrate(drizzle(legacy), { migrationsFolder: migrations });
      legacy.pragma('foreign_keys = OFF');
      for (const table of tables) for (const row of original[table] ?? []) {
        legacy.prepare(`INSERT INTO ${table} (${Object.keys(row).join(',')}) VALUES (${Object.keys(row).map(() => '?').join(',')})`).run(...Object.values(row));
      }
      legacy.close();
      const upgraded = makeApp({ databasePath: legacyPath });
      await upgraded.close();
      const db = new Database(legacyPath);
      try {
        for (const table of tables) expect(db.prepare(`SELECT * FROM ${table}`).all()).toEqual(original[table]);
        expect(db.pragma('foreign_key_check')).toEqual([]);
        expect(db.pragma('integrity_check', { simple: true })).toBe('ok');
        expect(() => db.exec("INSERT INTO market_prices VALUES ('DOGEUSDT', '1', 'now', 'test')")).toThrow();
        db.exec("INSERT INTO market_prices VALUES ('SOLUSDT', '100', 'now', 'test')");
      } finally { db.close(); }
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });
});
