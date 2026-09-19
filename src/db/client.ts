import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import { decimal } from '../shared/decimal.js';
import { LIMITS } from '../risk/limits.js';
import * as schema from './schema.js';

export function openDatabase(path: string, now = new Date()) {
  if (path !== ':memory:') mkdirSync(dirname(resolve(path)), { recursive: true });
  const sqlite = new Database(path);
  try {
    sqlite.pragma('foreign_keys = ON');
    sqlite.pragma('busy_timeout = 5000');
    sqlite.pragma('journal_mode = WAL');
    sqlite.pragma('synchronous = FULL');
    const db = drizzle(sqlite, { schema });
    // The same relative location works from src/db and dist/db.
    // SQLite table rebuilds require foreign keys disabled outside the migration
    // transaction. The rebuild migration checks all references before commit.
    sqlite.pragma('foreign_keys = OFF');
    try {
      migrate(db, { migrationsFolder: fileURLToPath(new URL('../../drizzle/', import.meta.url)) });
    } finally { sqlite.pragma('foreign_keys = ON'); }
    if ((sqlite.pragma('foreign_key_check') as unknown[]).length) throw new Error('Migration left invalid foreign keys.');
    db.insert(schema.portfolios).values({
      id: 1, initialCapital: decimal(LIMITS.initialCapitalUsd), cash: decimal(LIMITS.initialCapitalUsd),
      realizedPnl: decimal(0), totalFees: decimal(0), version: 0,
      createdAt: now.toISOString(), updatedAt: now.toISOString(),
    }).onConflictDoNothing().run();
    return { db, sqlite, close: () => sqlite.close() };
  } catch (error) {
    sqlite.close();
    throw error;
  }
}
export type DatabaseConnection = ReturnType<typeof openDatabase>;
