import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, mkdirSync, copyFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { makeApp, metadata, order } from './helpers.js';
import type { ContextResponse, DecisionsResponse, SignalResponse } from './helpers.js';
import { PaperTradeExecutor } from '../src/execution/paper-executor.js';
import type { ReviewService } from '../src/services/review-service.js';

type Report = ReturnType<ReviewService['report']>;
const strategy = { strategyId: 'momentum', strategyVersion: 'v1' };

describe('durable signal audit and review', () => {
  let directory: string;
  let databasePath: string;
  let app: ReturnType<typeof makeApp>;
  let now: Date;
  beforeEach(() => {
    directory = mkdtempSync(join(tmpdir(), 'paper-audit-test-'));
    databasePath = join(directory, 'paper.sqlite');
    now = new Date('2026-09-09T12:00:00.000Z');
    app = makeApp({ databasePath, clock: () => now });
  });
  afterEach(async () => { await app.close(); rmSync(directory, { recursive: true, force: true }); });
  const submit = (payload: Record<string, unknown>, key?: string) => app.inject({
    method: 'POST', url: '/api/signals', payload, headers: key === undefined ? {} : { 'idempotency-key': key },
  });
  const context = async () => (await app.inject('/api/context?BTCUSDT=100000')).json<ContextResponse>();
  const review = async (query = '') => (await app.inject(`/api/review${query}`)).json<Report>();

  it('replays the original result after portfolio changes and process restart', async () => {
    const before = await context();
    const payload = order({ ...strategy, contextId: before.contextId });
    const original = await submit(payload, 'run:1');
    expect(original.statusCode).toBe(200);
    expect(original.headers['idempotency-replayed']).toBe('false');
    await submit(order({ action: 'SELL', amountUsd: 2, price: 200000 }), 'run:2');
    await app.close();
    app = makeApp({ databasePath, clock: () => now });
    const retry = await submit({ ...payload, amountUsd: '5.000000', price: '100000' }, 'run:1');
    expect(retry.statusCode).toBe(200);
    expect(retry.json<unknown>()).toEqual(original.json<unknown>());
    expect(retry.headers['idempotency-replayed']).toBe('true');
    expect((await context()).portfolio.cash).toBe(46.993);
    expect((await app.inject('/api/decisions')).json<DecisionsResponse>().decisions).toHaveLength(2);
    expect((await app.inject(`/api/contexts/${before.contextId}`)).json<unknown>()).toEqual(before);
  });

  it('serializes simultaneous retries into a single fill and decision', async () => {
    const responses = await Promise.all(Array.from({ length: 15 }, () => submit(order(), 'same-run')));
    expect(responses.every((response) => response.statusCode === 200)).toBe(true);
    expect(new Set(responses.map((response) => response.json<SignalResponse>().decisionId)).size).toBe(1);
    expect(responses.filter((response) => response.headers['idempotency-replayed'] === 'false')).toHaveLength(1);
    expect((await context()).portfolio.cash).toBe(44.995);
  });

  it('recovers the winning receipt if another writer commits during paper execution', async () => {
    const peer = makeApp({ databasePath, clock: () => now });
    await app.close();
    app = makeApp({ databasePath, clock: () => now, executor: { execute: async (signal) => {
      expect((await peer.inject({ method: 'POST', url: '/api/signals', payload: order(), headers: { 'idempotency-key': 'race' } })).statusCode).toBe(200);
      return new PaperTradeExecutor().execute(signal);
    } } });
    try {
      const result = await submit(order(), 'race');
      expect(result.statusCode).toBe(200);
      expect(result.headers['idempotency-replayed']).toBe('true');
      expect((await context()).portfolio.cash).toBe(44.995);
      expect((await review()).summary.decisions).toBe(1);
    } finally { await peer.close(); }
  });

  it.each([{ amountUsd: 4 }, { rationale: 'Changed reason' }, { ...strategy }, { source: 'different-agent' }])(
    'rejects key reuse with different effective payload: %j', async (change) => {
      await submit(order(), 'fixed');
      const result = await submit(order(change), 'fixed');
      expect(result.statusCode).toBe(409);
      expect(result.json<unknown>()).toMatchObject({ error: { code: 'IDEMPOTENCY_CONFLICT' } });
      expect((await review()).summary.decisions).toBe(1);
    });

  it('replays HOLD and risk rejection without adding audit rows', async () => {
    for (const [key, payload, code] of [
      ['hold', { action: 'HOLD', ...metadata }, 200], ['reject', order({ amountUsd: 6 }), 422],
    ] as const) {
      const first = await submit(payload, key);
      const again = await submit(payload, key);
      expect(again.statusCode).toBe(code);
      expect(again.body).toBe(first.body);
      expect(again.headers['idempotency-replayed']).toBe('true');
    }
    expect((await review()).summary).toMatchObject({ decisions: 2, held: 1, rejected: 1, executed: 0 });
  });

  it('rolls back financial and audit writes on receipt failure and permits retry', async () => {
    const db = new Database(databasePath);
    try {
      db.exec("CREATE TRIGGER fail_receipt BEFORE INSERT ON signal_receipts BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END");
      for (const payload of [order(), order({ action: 'HOLD' }), order({ amountUsd: 6 })]) {
        expect((await submit(payload, 'retry')).statusCode).toBe(500);
      }
      for (const table of ['trades', 'agent_decisions', 'signal_receipts', 'positions', 'market_prices']) {
        expect(db.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get()).toEqual({ count: 0 });
      }
      expect((await context()).portfolio).toMatchObject({ cash: 50, version: 0 });
      db.exec('DROP TRIGGER fail_receipt');
      expect((await submit(order(), 'retry')).statusCode).toBe(200);
    } finally { db.close(); }
  });

  it('keeps legacy callers working and does not deduplicate without a key', async () => {
    await submit(order()); await submit(order());
    expect((await context()).portfolio.cash).toBe(39.99);
    const report = await review();
    expect(report.strategies[0]).toMatchObject({ strategyId: 'default', strategyVersion: 'unversioned', decisions: 2 });
    expect(report.findings.map((item) => item.code)).toEqual(expect.arrayContaining(['MISSING_IDEMPOTENCY', 'UNVERSIONED_STRATEGY', 'MISSING_AGENT_CONTEXT']));
  });

  it.each([
    [order({ strategyId: 'momentum' }), 'valid'], [order({ strategyVersion: 'v1' }), 'valid'],
    [order({ ...strategy, strategyVersion: 'bad version' }), 'valid'], [order({ contextId: 'bad' }), 'valid'],
    [order(), 'bad key'], [order(), 'x'.repeat(129)],
  ])('rejects invalid metadata or key without reserving a receipt', async (payload, key) => {
    expect((await submit(payload, key)).statusCode).toBe(400);
    expect((await review()).summary.decisions).toBe(0);
  });

  it('returns explicit missing-context errors and allows the key to be reused after correction', async () => {
    expect((await submit(order({ contextId: randomUUID() }), 'repair')).statusCode).toBe(404);
    expect((await submit(order(), 'repair')).statusCode).toBe(200);
    expect((await app.inject(`/api/contexts/${randomUUID()}`)).statusCode).toBe(404);
    expect((await app.inject(`/api/decisions/${randomUUID()}`)).statusCode).toBe(404);
    expect((await app.inject('/api/decisions/bad')).statusCode).toBe(400);
  });

  it('stores the exact linked agent response and decimal execution state before each outcome', async () => {
    const before = await context();
    for (const payload of [order(), order({ action: 'HOLD' }), order({ amountUsd: 6 })]) {
      const response = await submit({ ...payload, ...strategy, contextId: before.contextId }, randomUUID());
      const detail = (await app.inject(`/api/decisions/${response.json<SignalResponse>().decisionId}`)).json<{
        agentContext: unknown; decision: { executionContext: { portfolio: { cash: string }; riskDecision: unknown }; strategyId: string; strategyVersion: string };
      }>();
      expect(detail.agentContext).toEqual(before);
      expect(detail.decision).toMatchObject(strategy);
      expect(detail.decision.executionContext.portfolio.cash).toBe(payload.action === 'BUY' && payload.amountUsd === 5 ? '50' : '44.995');
      expect(detail.decision.executionContext.riskDecision).toBeDefined();
    }
  });

  it('reports stale and changed context with decision evidence, and never mutates the database', async () => {
    const before = await context();
    await submit(order({ ...strategy, contextId: before.contextId }), 'buy');
    now = new Date('2026-09-09T12:06:00.000Z');
    const sell = (await submit(order({ ...strategy, contextId: before.contextId, action: 'SELL', amountUsd: 2, price: 200000 }), 'sell')).json<SignalResponse>();
    const db = new Database(databasePath);
    try {
      const dump = () => db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").all()
        .map((row) => { const { name } = row as { name: string }; return db.prepare(`SELECT * FROM "${name.replaceAll('"', '""')}"`).all(); });
      const state = dump();
      const report = await review();
      expect(report.summary).toMatchObject({ decisions: 2, realizedPnlUsd: '0.997', feesPaidUsd: '0.007', profitableSellRate: 1 });
      for (const code of ['STALE_AGENT_CONTEXT', 'PORTFOLIO_CHANGED_SINCE_CONTEXT']) {
        expect(report.findings.find((item) => item.code === code)?.evidence).toEqual({ count: 1, decisionIds: [sell.decisionId] });
      }
      expect(report.findings.some((item) => item.code === 'MISSING_IDEMPOTENCY')).toBe(false);
      expect(await review()).toEqual(report);
      expect(dump()).toEqual(state);
    } finally { db.close(); }
  });

  it('filters strategy versions and time windows and flags repeated risk failures', async () => {
    now = new Date('2026-09-01T00:00:00.000Z');
    await submit(order({ ...strategy, action: 'HOLD' }), 'old');
    now = new Date('2026-09-09T12:00:00.000Z');
    for (let i = 0; i < 3; i++) await submit(order({ ...strategy, amountUsd: 6 }), `bad-${i}`);
    await submit(order({ ...strategy, strategyVersion: 'v2', action: 'HOLD' }), 'v2');
    const report = await review('?days=7&strategyId=momentum&strategyVersion=v1');
    expect(report.summary).toMatchObject({ decisions: 3, rejected: 3, rejectionRate: 1 });
    expect(report.findings.find((item) => item.code === 'REPEATED_ORDER_TOO_LARGE')?.evidence.count).toBe(3);
    expect((await review('?days=30')).summary.decisions).toBe(5);
    for (const query of ['?days=0', '?days=91', '?strategyVersion=v1', '?override=true']) {
      expect((await app.inject(`/api/review${query}`)).statusCode).toBe(400);
    }
  });

  it('bounds large reviews and explicitly labels sample truncation', async () => {
    const db = new Database(databasePath);
    try {
      const insert = db.prepare(`INSERT INTO agent_decisions
        (id, action, confidence, risk_level, rationale, source, status, created_at) VALUES (?, 'HOLD', 0.5, 'LOW', 'Legacy sample', 'test', 'HOLD', ?)`);
      db.transaction(() => { for (let i = 0; i < 1001; i++) insert.run(randomUUID(), now.toISOString()); })();
    } finally { db.close(); }
    const report = await review();
    expect(report.window.truncated).toBe(true);
    expect(report.summary.decisions).toBe(1000);
    expect(report.findings.find((item) => item.code === 'MISSING_EXECUTION_SNAPSHOT')?.evidence.count).toBe(1000);
    expect(report.findings.every((item) => item.evidence.decisionIds.length <= 5)).toBe(true);
  });

  it('reset clears receipts and linked contexts so old keys cannot replay deleted history', async () => {
    const before = await context();
    await submit(order({ contextId: before.contextId }), 'reset-run');
    await app.close();
    execFileSync(process.execPath, ['--import', 'tsx', 'scripts/reset.ts'], {
      cwd: process.cwd(), env: { ...process.env, DATABASE_PATH: databasePath }, windowsHide: true,
    });
    app = makeApp({ databasePath });
    expect((await app.inject(`/api/contexts/${before.contextId}`)).statusCode).toBe(404);
    const result = await submit(order(), 'reset-run');
    expect(result.statusCode).toBe(200);
    expect(result.headers['idempotency-replayed']).toBe('false');
    expect((await context()).portfolio.cash).toBe(44.995);
  });

  it('upgrades the previous schema without inventing legacy context or changing the portfolio', async () => {
    await app.close();
    const legacyPath = join(directory, 'legacy.sqlite');
    const migrationDirectory = join(directory, 'migrations');
    mkdirSync(join(migrationDirectory, 'meta'), { recursive: true });
    copyFileSync('drizzle/0000_whole_mindworm.sql', join(migrationDirectory, '0000_whole_mindworm.sql'));
    const journal = JSON.parse(readFileSync('drizzle/meta/_journal.json', 'utf8')) as { entries: unknown[] };
    writeFileSync(join(migrationDirectory, 'meta/_journal.json'), JSON.stringify({ ...journal, entries: journal.entries.slice(0, 1) }));
    const legacy = new Database(legacyPath);
    const decisionId = randomUUID();
    try {
      migrate(drizzle(legacy), { migrationsFolder: migrationDirectory });
      legacy.prepare("INSERT INTO portfolio VALUES (1, '50', '42', '0', '0', 7, ?, ?)").run(now.toISOString(), now.toISOString());
      legacy.prepare(`INSERT INTO agent_decisions (id, action, confidence, risk_level, rationale, source, status, created_at)
        VALUES (?, 'HOLD', 0.5, 'LOW', 'Original historical decision', 'legacy', 'HOLD', ?)`).run(decisionId, now.toISOString());
    } finally { legacy.close(); }
    app = makeApp({ databasePath: legacyPath, clock: () => now });
    expect((await context()).portfolio).toMatchObject({ cash: 42, version: 7 });
    expect((await app.inject(`/api/decisions/${decisionId}`)).json<unknown>()).toMatchObject({
      decision: { rationale: 'Original historical decision', strategyId: 'default', strategyVersion: 'unversioned', executionContext: null, contextId: null }, agentContext: null,
    });
  });
});
