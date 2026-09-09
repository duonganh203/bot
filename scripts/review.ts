import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { loadEnv } from '../src/config/env.js';
import { openDatabase } from '../src/db/client.js';
import { SqliteReviewRepository } from '../src/repositories/sqlite/review-repository.js';
import { reviewQuerySchema } from '../src/review/options.js';
import { ReviewService } from '../src/services/review-service.js';

const { values } = parseArgs({ options: {
  days: { type: 'string' }, strategyId: { type: 'string' }, strategyVersion: { type: 'string' },
} });
const options = reviewQuerySchema.parse(values);
const connection = openDatabase(loadEnv().DATABASE_PATH);
try {
  const report = new ReviewService(new SqliteReviewRepository(connection)).report(options);
  const directory = resolve('data/reviews');
  mkdirSync(directory, { recursive: true });
  const path = resolve(directory, `${report.generatedAt.replaceAll(':', '-')}-${randomUUID()}.json`);
  const json = `${JSON.stringify(report, null, 2)}\n`;
  writeFileSync(path, json, { flag: 'wx' });
  writeFileSync(resolve(directory, 'latest.json'), json);
  console.log(`Review saved to ${path}`);
  console.log(`${report.summary.decisions} decisions; ${report.findings.length} findings; truncated=${report.window.truncated}`);
} finally { connection.close(); }
