import type { FastifyInstance } from 'fastify';
import { SYMBOLS } from '../../domain/trading/types.js';
import type { PriceBook } from '../../domain/trading/types.js';
import type { HistoryService } from '../../services/history-service.js';
import type { PortfolioService } from '../../services/portfolio-service.js';
import type { TradingService } from '../../services/trading-service.js';
import { contextQuerySchema, decisionsQuerySchema, idParamsSchema, signalHeadersSchema, signalSchema, tradesQuerySchema } from './schemas.js';
import { present } from './presenters.js';
import type { ReviewService } from '../../services/review-service.js';
import { reviewQuerySchema } from '../../review/options.js';

export function registerRoutes(app: FastifyInstance, services: {
  portfolio: PortfolioService;
  trading: TradingService;
  history: HistoryService;
  review: ReviewService;
  clock: () => Date;
}): void {
  app.addHook('onSend', (_request, reply, payload, done) => {
    reply.header('Cache-Control', 'no-store');
    done(null, payload);
  });
  app.get('/health', () => ({ status: 'ok' }));

  app.get('/api/review', (request) => services.review.report(reviewQuerySchema.parse(request.query)));

  app.get('/api/context', async (request) => {
    const query = contextQuerySchema.parse(request.query);
    const overrides: PriceBook = {};
    for (const symbol of SYMBOLS) {
      const price = query[symbol];
      if (price) overrides[symbol] = { price, asOf: services.clock().toISOString(), source: 'query' };
    }
    return present(await services.portfolio.context(overrides));
  });

  app.post('/api/signals', async (request, reply) => {
    const signal = signalSchema.parse(request.body);
    const headers = signalHeadersSchema.parse(request.headers);
    const submission = await services.trading.submit(signal, headers['idempotency-key']);
    return reply.code(submission.result.status === 'rejected' ? 422 : 200)
      .header('Idempotency-Replayed', String(submission.replayed)).send(present(submission.result));
  });

  app.get('/api/trades', (request) => {
    const filter = tradesQuerySchema.parse(request.query);
    return { trades: present(services.history.trades(filter)) };
  });

  app.get('/api/decisions', (request) => {
    const { limit } = decisionsQuerySchema.parse(request.query);
    return { decisions: present(services.history.decisions(limit)) };
  });

  app.get('/api/decisions/:id', (request) => {
    const { id } = idParamsSchema.parse(request.params);
    return present(services.history.decision(id));
  });

  app.get('/api/contexts/:id', (request) => {
    const { id } = idParamsSchema.parse(request.params);
    return present(services.history.context(id));
  });
}
