import { loadEnv } from '../src/config/env.js';
import { openDatabase } from '../src/db/client.js';
import { agentDecisions, marketPrices, portfolios, positions, trades } from '../src/db/schema.js';
import { decimal } from '../src/shared/decimal.js';
import { LIMITS } from '../src/risk/limits.js';
import { sql } from 'drizzle-orm';

const env = loadEnv();
const connection = openDatabase(env.DATABASE_PATH);
try {
  connection.db.transaction((tx) => {
    tx.delete(agentDecisions).run();
    tx.delete(trades).run();
    tx.delete(positions).run();
    tx.delete(marketPrices).run();
    tx.update(portfolios).set({
      cash: decimal(LIMITS.initialCapitalUsd), realizedPnl: decimal(0), totalFees: decimal(0),
      version: sql`${portfolios.version} + 1`, updatedAt: new Date().toISOString(),
    }).run();
  }, { behavior: 'immediate' });
  console.log(`Reset paper portfolio to $50 in ${env.DATABASE_PATH}`);
} finally {
  connection.close();
}
