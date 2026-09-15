import { timingSafeEqual } from 'node:crypto';
import Fastify from 'fastify';
import { ZodError } from 'zod';
import { openDatabase } from '../db/client.js';
import { PaperTradeExecutor } from '../execution/paper-executor.js';
import type { TradeExecutor } from '../execution/trade-executor.js';
import type { MarketDataProvider } from '../market/market-data-provider.js';
import { SqliteTradingRepository } from '../repositories/sqlite/trading-repository.js';
import { PortfolioService } from '../services/portfolio-service.js';
import { TradingService } from '../services/trading-service.js';
import { HistoryService } from '../services/history-service.js';
import { ReviewService } from '../services/review-service.js';
import { SqliteReviewRepository } from '../repositories/sqlite/review-repository.js';
import { AppError } from '../shared/errors.js';
import { RiskEngine } from '../risk/risk-engine.js';
import type { RiskPolicy } from '../risk/limits.js';
import { registerRoutes } from './routes/trading.js';

export interface AppOptions {
  databasePath: string;
  logger?: boolean | { level: string };
  apiToken?: string;
  clock?: () => Date;
  executor?: TradeExecutor;
  marketData?: MarketDataProvider;
  riskPolicy?: RiskPolicy;
}

export function buildApp(options: AppOptions) {
  const clock = options.clock ?? (() => new Date());
  const connection = openDatabase(options.databasePath, clock());
  const repository = new SqliteTradingRepository(connection);
  const app = Fastify({ logger: options.logger ?? false, bodyLimit: 16_384, requestTimeout: 15_000 });
  const trading = new TradingService(repository, options.executor ?? new PaperTradeExecutor(), new RiskEngine(undefined, options.riskPolicy), clock, options.marketData);
  const portfolio = new PortfolioService(repository, clock, options.marketData, undefined, options.riskPolicy);

  if (options.apiToken) {
    const expected = Buffer.from(`Bearer ${options.apiToken}`);
    app.addHook('onRequest', (request, reply, done) => {
      if (request.url.split('?')[0] === '/health') {
        done();
        return;
      }
      const actual = Buffer.from(request.headers.authorization ?? '');
      if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
        void reply.code(401).send({ error: { code: 'UNAUTHORIZED', message: 'A valid bearer token is required.' } });
        return;
      }
      done();
    });
  }

  app.setErrorHandler((error, request, reply) => {
    if (error instanceof ZodError) {
      return reply.code(400).send({ error: { code: 'VALIDATION_ERROR', message: 'Invalid request.', issues: error.issues } });
    }
    if (error instanceof AppError) {
      return reply.code(error.statusCode).send({ error: { code: error.code, message: error.message } });
    }
    if (error instanceof Error && 'statusCode' in error && typeof error.statusCode === 'number' && error.statusCode < 500) {
      return reply.code(error.statusCode).send({ error: { code: 'INVALID_REQUEST', message: error.message } });
    }
    request.log.error({ err: error }, 'Request failed');
    return reply.code(500).send({ error: { code: 'INTERNAL_ERROR', message: 'An internal error occurred.' } });
  });
  registerRoutes(app, { portfolio, trading, history: new HistoryService(repository),
    review: new ReviewService(new SqliteReviewRepository(connection), clock), clock });
  app.addHook('onClose', () => { connection.close(); });
  return app;
}
