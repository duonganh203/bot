import { randomUUID } from 'node:crypto';
import { applyFill } from '../domain/trading/accounting.js';
import { isTradingSymbol } from '../domain/trading/types.js';
import type { AgentDecision, DecisionAudit, Signal, SignalResult } from '../domain/trading/types.js';
import { signalHash } from '../domain/trading/idempotency.js';
import { loadMarks, valuePortfolio } from '../domain/trading/valuation.js';
import type { Marks } from '../domain/trading/valuation.js';
import type { TradeExecutor } from '../execution/trade-executor.js';
import { ManualMarketDataProvider } from '../market/market-data-provider.js';
import type { MarketDataProvider } from '../market/market-data-provider.js';
import type { TradingRepository } from '../repositories/trading-repository.js';
import { RiskEngine } from '../risk/risk-engine.js';
import { SerialQueue } from '../shared/serial-queue.js';
import { AppError } from '../shared/errors.js';
import { toJson } from '../shared/json.js';
import { captureDecisionContext } from './decision-audit.js';
import { contextMarks } from './context-marks.js';

export class TradingService {
  private readonly queue = new SerialQueue();

  constructor(
    private readonly repository: TradingRepository,
    private readonly executor: TradeExecutor,
    private readonly risk = new RiskEngine(),
    private readonly clock: () => Date = () => new Date(),
    private readonly marketData?: MarketDataProvider,
  ) {}

  submit(signal: Signal, idempotencyKey?: string) {
    return this.queue.run(async () => {
      const hash = signalHash(signal);
      if (idempotencyKey) {
        const replay = this.replay(idempotencyKey, hash);
        if (replay) return replay;
      }
      try {
        return { result: await this.process(signal, hash, idempotencyKey), replayed: false };
      } catch (error) {
        // A concurrent writer can win while the pure paper executor is awaiting.
        // Read its receipt only after our failed transaction has rolled back.
        if (idempotencyKey) {
          const replay = this.replay(idempotencyKey, hash);
          if (replay) return replay;
        }
        throw error;
      }
    });
  }

  private replay(key: string, hash: string) {
    const receipt = this.repository.getReceipt(key);
    if (!receipt) return undefined;
    if (receipt.requestHash !== hash) {
      throw new AppError('IDEMPOTENCY_CONFLICT', 'This Idempotency-Key was already used for a different signal.', 409);
    }
    return { result: receipt.result, replayed: true };
  }

  private async process(signal: Signal, hash: string, idempotencyKey?: string): Promise<SignalResult> {
    const now = this.clock();
    const timestamp = now.toISOString();
    const snapshot = this.repository.snapshot(now);
    const agentContext = signal.contextId ? this.repository.getContext(signal.contextId) : undefined;
    if (signal.contextId && !agentContext) {
      throw new AppError('CONTEXT_NOT_FOUND', 'The supplied contextId does not exist. Fetch context before creating a signal.', 404);
    }
    const decision: AgentDecision = {
      id: randomUUID(), signal, status: 'HOLD', tradeId: null,
      rejectionCode: null, rejectionReason: null, createdAt: timestamp,
    };
    const receiptFor = (result: SignalResult) => idempotencyKey ? {
      key: idempotencyKey, requestHash: hash, decisionId: decision.id, result, createdAt: timestamp,
    } : undefined;
    if (signal.action === 'HOLD') {
      const result: SignalResult = { status: 'held', decisionId: decision.id };
      this.repository.saveDecision(decision, {
        executionContext: captureDecisionContext(snapshot, now, new Map(), null, agentContext, this.marketData ? 'provider' : 'manual', this.risk.policy),
        receipt: receiptFor(result),
      });
      return result;
    }
    const prices = this.risk.policy === 'reduce-only-v2'
      ? contextMarks(agentContext, snapshot, signal, now) : { ...snapshot.prices };
    if (isTradingSymbol(signal.symbol)) {
      prices[signal.symbol] = { price: signal.price, asOf: timestamp, source: 'signal' };
    }
    const marks = await loadMarks(snapshot.positions, this.marketData ?? new ManualMarketDataProvider(prices));
    const risk = this.risk.evaluate(signal, snapshot, marks, now);
    const auditMarks: Marks = isTradingSymbol(signal.symbol) && !marks.has(signal.symbol)
      ? new Map(marks).set(signal.symbol, signal.price) : marks;
    const executionContext = captureDecisionContext(
      { ...snapshot, prices }, now, auditMarks, risk, agentContext, this.marketData ? 'provider' : 'manual', this.risk.policy,
    );
    if (!risk.approved) {
      const result: SignalResult = { status: 'rejected', decisionId: decision.id, risk: { code: risk.code, reason: risk.reason } };
      this.repository.saveDecision(
        { ...decision, status: 'REJECTED', rejectionCode: risk.code, rejectionReason: risk.reason },
        { executionContext, receipt: receiptFor(result) },
      );
      return result;
    }
    // Type narrowing is explicit; only backend-approved symbols reach execution.
    if (!isTradingSymbol(signal.symbol)) throw new Error('Risk engine approved an invalid symbol');
    const fill = await this.executor.execute({ ...signal, symbol: signal.symbol });
    const applied = applyFill(
      snapshot.portfolio, snapshot.positions.find((position) => position.symbol === signal.symbol),
      fill, randomUUID(), timestamp,
    );
    const executedDecision: AgentDecision = { ...decision, status: 'EXECUTED', tradeId: applied.trade.id };
    const updatedPositions = snapshot.positions.filter((position) => position.symbol !== fill.symbol);
    if (applied.position.quantity.isPositive()) updatedPositions.push(applied.position);
    const updatedMarks = new Map(marks).set(fill.symbol, fill.price);
    const result: SignalResult = {
      status: 'executed', decisionId: decision.id, trade: toJson(applied.trade),
      portfolio: toJson(valuePortfolio(applied.portfolio, updatedPositions, updatedMarks)),
    };
    const audit: DecisionAudit = { executionContext, receipt: receiptFor(result) };
    this.repository.commitTrade({
      ...applied, expectedVersion: snapshot.portfolio.version, decision: executedDecision, audit,
      quote: { price: fill.price, asOf: timestamp, source: 'paper-fill' },
    });
    return result;
  }
}
