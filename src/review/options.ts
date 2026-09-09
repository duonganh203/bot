import { z } from 'zod';

const strategyLabel = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/);
export const reviewQuerySchema = z.strictObject({
  days: z.coerce.number().int().min(1).max(90).default(7),
  strategyId: strategyLabel.optional(),
  strategyVersion: strategyLabel.optional(),
}).refine((value) => !value.strategyVersion || Boolean(value.strategyId), 'strategyVersion requires strategyId');
export type ReviewOptions = z.infer<typeof reviewQuerySchema>;
