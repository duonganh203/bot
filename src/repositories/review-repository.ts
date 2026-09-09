import type { Amount } from '../shared/decimal.js';

export interface ReviewObservation {
  id: string;
  createdAt: string;
  action: string;
  status: string;
  rejectionCode: string | null;
  strategyId: string;
  strategyVersion: string;
  contextId: string | null;
  idempotencyKey: string | null;
  contextCreatedAt: string | null;
  contextPortfolioVersion: number | null;
  executionPortfolioVersion: number | null;
  feeUsd: Amount | null;
  realizedPnl: Amount | null;
}
export interface ReviewFilter {
  from: string;
  to: string;
  strategyId?: string | undefined;
  strategyVersion?: string | undefined;
}
export interface ReviewRepository {
  observations(filter: ReviewFilter, limit: number): ReviewObservation[];
}
