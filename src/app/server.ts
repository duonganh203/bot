import { buildApp } from './build-app.js';
import { loadEnv } from '../config/env.js';

const env = loadEnv();
const app = buildApp({ databasePath: env.DATABASE_PATH, logger: { level: env.LOG_LEVEL }, apiToken: env.API_TOKEN });

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.once(signal, () => {
    app.log.info({ signal }, 'Shutting down');
    void app.close().catch((error: unknown) => {
      app.log.error({ err: error }, 'Shutdown failed');
      process.exitCode = 1;
    });
  });
}

try {
  await app.listen({ host: env.HOST, port: env.PORT });
} catch (error) {
  app.log.error({ err: error }, 'Startup failed');
  await app.close();
  process.exitCode = 1;
}
