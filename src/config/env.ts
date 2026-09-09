import 'dotenv/config';
import { z } from 'zod';

const envSchema = z.object({
  HOST: z.string().min(1).default('127.0.0.1'),
  PORT: z.coerce.number().int().min(1).max(65535).default(3000),
  DATABASE_PATH: z.string().min(1).default('./data/paper-trader.sqlite'),
  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace', 'silent']).default('info'),
  API_TOKEN: z.string().default(''),
});

export function loadEnv(input: NodeJS.ProcessEnv = process.env) {
  return envSchema.parse(input);
}
