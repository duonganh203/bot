import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { join, resolve } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import Database from 'better-sqlite3';
import { z } from 'zod';

const dataDirectory = resolve('data');
mkdirSync(dataDirectory, { recursive: true });
const directory = mkdtempSync(join(dataDirectory, 'smoke-'));
const databasePath = join(directory, 'paper.sqlite');
const checks: string[] = [];
const exchanges: { method: string; path: string; status: number; body: unknown }[] = [];
let logs = '';

const portfolioSchema = z.object({ cash: z.number(), realizedPnl: z.number(), totalFees: z.number() });
const contextSchema = z.object({ portfolio: portfolioSchema, positions: z.array(z.object({ quantity: z.number() })) });
const executionSchema = z.object({ status: z.literal('executed'), portfolio: portfolioSchema, trade: z.object({ feeUsd: z.number(), realizedPnl: z.number() }) });

async function availablePort(): Promise<number> {
  const socket = createServer();
  await new Promise<void>((resolveListen, reject) => {
    socket.once('error', reject);
    socket.listen(0, '127.0.0.1', resolveListen);
  });
  const address = socket.address();
  if (!address || typeof address === 'string') throw new Error('No TCP port assigned');
  await new Promise<void>((resolveClose, reject) => { socket.close((error) => { if (error) reject(error); else resolveClose(); }); });
  return address.port;
}

async function startServer() {
  const port = await availablePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const child = spawn(process.execPath, ['dist/app/server.js'], {
    cwd: process.cwd(), windowsHide: true,
    env: { ...process.env, HOST: '127.0.0.1', PORT: String(port), DATABASE_PATH: databasePath, LOG_LEVEL: 'info', API_TOKEN: '' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.setEncoding('utf8').on('data', (chunk: string) => { logs += chunk; });
  child.stderr.setEncoding('utf8').on('data', (chunk: string) => { logs += chunk; });
  const closed = new Promise<void>((resolveClose) => { child.once('close', () => { resolveClose(); }); });
  let spawnError: Error | undefined;
  child.once('error', (error) => { spawnError = error; });
  const stop = async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
    const escalation = globalThis.setTimeout(() => { child.kill('SIGKILL'); }, 5000);
    try { await closed; } finally { clearTimeout(escalation); }
  };
  for (let attempt = 0; attempt < 100; attempt++) {
    if (spawnError || child.exitCode !== null) {
      await stop();
      throw spawnError ?? new Error(`Server exited during startup: ${logs}`);
    }
    try {
      const response = await fetch(`${baseUrl}/health`, { signal: AbortSignal.timeout(500) });
      if (response.ok) return { baseUrl, stop };
    } catch { /* server is still starting */ }
    await setTimeout(100);
  }
  await stop();
  throw new Error(`Server readiness timed out: ${logs}`);
}

async function request(baseUrl: string, path: string, payload?: unknown, expectedStatus = 200): Promise<unknown> {
  const method = payload === undefined ? 'GET' : 'POST';
  const response = await fetch(`${baseUrl}${path}`, {
    method, signal: AbortSignal.timeout(5000),
    ...(payload === undefined ? {} : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) }),
  });
  const body: unknown = await response.json();
  exchanges.push({ method, path, status: response.status, body });
  assert.equal(response.status, expectedStatus, JSON.stringify(body));
  return body;
}

const signal = { action: 'BUY', symbol: 'BTCUSDT', amountUsd: 5, price: 100000,
  confidence: 0.74, riskLevel: 'LOW', rationale: 'HTTP smoke test', source: 'smoke' };

try {
  const server = await startServer();
  try {
    assert.deepEqual(await request(server.baseUrl, '/health'), { status: 'ok' });
    checks.push('GET /health');
    assert.equal(contextSchema.parse(await request(server.baseUrl, '/api/context')).portfolio.cash, 50);
    checks.push('GET /api/context: initial $50');

    const buy = executionSchema.parse(await request(server.baseUrl, '/api/signals', signal));
    assert.equal(buy.portfolio.cash, 44.995);
    assert.equal(buy.trade.feeUsd, 0.005);
    checks.push('BUY: cash 44.995, fee 0.005');

    const beforeHold = await request(server.baseUrl, '/api/context');
    assert.equal(z.object({ status: z.literal('held') }).parse(await request(server.baseUrl, '/api/signals', { ...signal, action: 'HOLD' })).status, 'held');
    const afterHold = await request(server.baseUrl, '/api/context');
    assert.deepEqual(contextSchema.parse(afterHold), contextSchema.parse(beforeHold));
    checks.push('HOLD: portfolio unchanged');

    const sell = executionSchema.parse(await request(server.baseUrl, '/api/signals', { ...signal, action: 'SELL', amountUsd: 2, price: 200000 }));
    assert.equal(sell.portfolio.cash, 46.993);
    assert.equal(sell.trade.realizedPnl, 0.997);
    checks.push('SELL: cash 46.993, realized PnL 0.997');

    const rejected = z.object({ status: z.literal('rejected'), risk: z.object({ code: z.literal('ORDER_TOO_LARGE') }) })
      .parse(await request(server.baseUrl, '/api/signals', { ...signal, amountUsd: 6 }, 422));
    assert.equal(rejected.risk.code, 'ORDER_TOO_LARGE');
    checks.push('Oversized order: HTTP 422 ORDER_TOO_LARGE');
    assert.equal(z.object({ trades: z.array(z.unknown()) }).parse(await request(server.baseUrl, '/api/trades')).trades.length, 2);
    assert.equal(z.object({ decisions: z.array(z.unknown()) }).parse(await request(server.baseUrl, '/api/decisions')).decisions.length, 4);
    checks.push('History: 2 trades, 4 decisions');
  } finally { await server.stop(); }

  const restarted = await startServer();
  try {
    const context = contextSchema.parse(await request(restarted.baseUrl, '/api/context'));
    assert.equal(context.portfolio.cash, 46.993);
    assert.equal(context.positions[0]?.quantity, 0.00004);
    checks.push('Production process restart preserves state');
  } finally { await restarted.stop(); }

  const sqlite = new Database(databasePath, { readonly: true });
  let state: unknown;
  try {
    const integrity: unknown = sqlite.pragma('integrity_check', { simple: true });
    const foreignKeys: unknown = sqlite.pragma('foreign_key_check');
    assert.equal(integrity, 'ok');
    assert.deepEqual(foreignKeys, []);
    const portfolio: unknown = sqlite.prepare('SELECT cash, realized_pnl, total_fees FROM portfolio').get();
    assert.deepEqual(portfolio, { cash: '46.993', realized_pnl: '0.997', total_fees: '0.007' });
    const positions: unknown = sqlite.prepare('SELECT symbol, quantity, average_entry_price FROM positions').all();
    assert.deepEqual(positions, [{ symbol: 'BTCUSDT', quantity: '0.00004', average_entry_price: '100000' }]);
    state = { integrity, foreignKeys, portfolio, positions,
      trades: sqlite.prepare('SELECT side, gross_usd, fee_usd, realized_pnl FROM trades ORDER BY sequence').all(),
      decisions: sqlite.prepare('SELECT action, status, rejection_code FROM agent_decisions ORDER BY sequence').all(),
    };
    checks.push('Direct SQLite state, decimal TEXT storage, integrity and foreign keys verified');
  } finally { sqlite.close(); }

  const report = { status: 'passed', testedAt: new Date().toISOString(), databasePath, checks, exchanges, sqlite: state };
  writeFileSync(join(directory, 'report.json'), JSON.stringify(report, null, 2));
  writeFileSync(join(dataDirectory, 'latest-smoke.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, checks, reportPath: join(directory, 'report.json'), databasePath }, null, 2));
} finally {
  writeFileSync(join(directory, 'server.log'), logs);
}
