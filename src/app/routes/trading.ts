import type { FastifyInstance } from 'fastify';
import { SYMBOLS } from '../../domain/trading/types.js';
import type { PriceBook } from '../../domain/trading/types.js';
import type { HistoryService } from '../../services/history-service.js';
import type { PortfolioService } from '../../services/portfolio-service.js';
import type { TradingService } from '../../services/trading-service.js';
import { contextQuerySchema, decisionsQuerySchema, signalSchema, tradesQuerySchema } from './schemas.js';
import { present } from './presenters.js';

export function registerRoutes(app: FastifyInstance, services: {
  portfolio: PortfolioService;
  trading: TradingService;
  history: HistoryService;
  clock: () => Date;
}): void {
  app.get('/health', () => ({ status: 'ok' }));

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
    const result = await services.trading.submit(signal);
    return reply.code(result.status === 'rejected' ? 422 : 200).send(present(result));
  });

  app.get('/api/trades', (request) => {
    const filter = tradesQuerySchema.parse(request.query);
    return { trades: present(services.history.trades(filter)) };
  });

  app.get('/api/decisions', (request) => {
    const { limit } = decisionsQuerySchema.parse(request.query);
    return { decisions: present(services.history.decisions(limit)) };
  });
}
