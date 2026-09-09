import type { ReviewObservation, ReviewRepository } from '../repositories/review-repository.js';
import type { ReviewOptions } from '../review/options.js';
import { decimal } from '../shared/decimal.js';

const MAX_DECISIONS = 1000;
const STALE_CONTEXT_SECONDS = 300;
interface Finding {
  code: string;
  severity: 'info' | 'warning';
  observation: string;
  evidence: { count: number; decisionIds: string[] };
  suggestedChange: string;
  validation: string;
}

function summarize(rows: ReviewObservation[]) {
  const executed = rows.filter((row) => row.status === 'EXECUTED');
  const sells = executed.filter((row) => row.action === 'SELL');
  const orders = rows.filter((row) => row.action !== 'HOLD');
  const rejected = rows.filter((row) => row.status === 'REJECTED');
  return {
    decisions: rows.length, orders: orders.length, executed: executed.length,
    buys: executed.filter((row) => row.action === 'BUY').length, sells: sells.length,
    held: rows.filter((row) => row.status === 'HOLD').length, rejected: rejected.length,
    rejectionRate: orders.length ? rejected.length / orders.length : null,
    profitableSellRate: sells.length ? sells.filter((row) => row.realizedPnl?.isPositive()).length / sells.length : null,
    realizedPnlUsd: executed.reduce((sum, row) => sum.plus(row.realizedPnl ?? 0), decimal(0)).toFixed(),
    feesPaidUsd: executed.reduce((sum, row) => sum.plus(row.feeUsd ?? 0), decimal(0)).toFixed(),
  };
}

export class ReviewService {
  constructor(private readonly repository: ReviewRepository, private readonly clock: () => Date = () => new Date()) {}

  report(options: ReviewOptions) {
    const now = this.clock();
    const filter = {
      from: new Date(now.getTime() - options.days * 86_400_000).toISOString(), to: now.toISOString(),
      strategyId: options.strategyId, strategyVersion: options.strategyVersion,
    };
    const fetched = this.repository.observations(filter, MAX_DECISIONS + 1);
    const rows = fetched.slice(0, MAX_DECISIONS);
    const summary = summarize(rows);
    const findings: Finding[] = [];
    const add = (code: string, matches: ReviewObservation[], observation: string, suggestedChange: string, validation: string) => {
      if (matches.length) findings.push({ code, severity: 'warning', observation,
        evidence: { count: matches.length, decisionIds: matches.slice(0, 5).map((row) => row.id) }, suggestedChange, validation });
    };
    add('MISSING_IDEMPOTENCY', rows.filter((row) => !row.idempotencyKey),
      'These signals were submitted without a durable retry key.',
      'Persist one Idempotency-Key and the exact payload per scheduled decision before submitting it.',
      'Retry across timeouts and restart; require exactly one decision and one fill.');
    add('UNVERSIONED_STRATEGY', rows.filter((row) => row.strategyVersion === 'unversioned'),
      'These decisions cannot be attributed to a named strategy revision.',
      'Send strategyId and strategyVersion and retain the corresponding prompt/configuration.',
      'Verify each decision links to an immutable strategy revision.');
    add('MISSING_AGENT_CONTEXT', rows.filter((row) => !row.contextId),
      'The input context used by the agent was not linked.',
      'Read context before reasoning and return its contextId with the signal.',
      'Retrieve the decision detail and compare agentContext with the original context response.');
    add('MISSING_EXECUTION_SNAPSHOT', rows.filter((row) => row.executionPortfolioVersion === null),
      'These decisions have no execution snapshot, including history from before the audit migration.',
      'Use complete new audit records for flow diagnosis; keep legacy gaps explicit.',
      'Check new HOLD, rejected, and executed decisions all contain an execution snapshot.');
    add('STALE_AGENT_CONTEXT', rows.filter((row) => row.contextCreatedAt !== null &&
      Date.parse(row.createdAt) - Date.parse(row.contextCreatedAt) > STALE_CONTEXT_SECONDS * 1000),
    'The linked context was more than five minutes old when the signal was processed.',
    'Fetch a new context and reconsider the decision when processing is delayed.',
    'Simulate a delayed run; confirm it creates a new decision/key only after reevaluation.');
    add('PORTFOLIO_CHANGED_SINCE_CONTEXT', rows.filter((row) => row.contextPortfolioVersion !== null &&
      row.executionPortfolioVersion !== null && row.contextPortfolioVersion !== row.executionPortfolioVersion),
    'Portfolio state changed between the agent context and backend processing.',
    'Serialize scheduled runs and refresh context after each completed decision.',
    'Test overlapping runs and compare the linked and execution portfolio versions.');
    const rejectionCounts: Record<string, number> = {};
    for (const row of rows) if (row.rejectionCode) rejectionCounts[row.rejectionCode] = (rejectionCounts[row.rejectionCode] ?? 0) + 1;
    for (const [code, count] of Object.entries(rejectionCounts).sort(([a], [b]) => a.localeCompare(b))) {
      if (count >= 3) add(`REPEATED_${code}`, rows.filter((row) => row.rejectionCode === code),
        `The backend rejected ${count} orders with ${code}.`,
        'Inspect decision evidence and adjust signal construction to the current risk budget; keep backend limits fixed.',
        'Replay the failing cases and boundary cases; require risk and accounting tests to pass.');
    }
    if (decimal(summary.realizedPnlUsd).isNegative()) add('NEGATIVE_REALIZED_PNL', rows.filter((row) => row.realizedPnl?.isNegative()),
      'Recorded fills have negative net realized PnL in this sample; this does not establish a cause.',
      'Compare a specific strategy hypothesis on separate paper data with the same fees, prices, and initial state.',
      'Use an evaluation window fixed in advance; include unrealized positions and drawdown before judging performance.');
    if (summary.sells < 20) findings.push({ code: 'LIMITED_OUTCOME_SAMPLE', severity: 'info',
      observation: 'Fewer than 20 executed SELL fills are available. This is a heuristic warning, not a statistical confidence threshold.',
      evidence: { count: summary.sells, decisionIds: rows.filter((row) => row.action === 'SELL' && row.status === 'EXECUTED').slice(0, 5).map((row) => row.id) },
      suggestedChange: 'Collect more independent paper outcomes before proposing a performance-based change.',
      validation: 'Do not promote a candidate on this report alone, even after reaching 20 sells.' });
    const groups = new Map<string, [ReviewObservation, ...ReviewObservation[]]>();
    for (const row of rows) {
      const key = `${row.strategyId}/${row.strategyVersion}`;
      const group = groups.get(key);
      if (group) group.push(row);
      else groups.set(key, [row]);
    }
    return {
      schemaVersion: 1, analysisVersion: '1', generatedAt: now.toISOString(), mode: 'read-only' as const,
      window: { ...filter, days: options.days, maxDecisions: MAX_DECISIONS, truncated: fetched.length > MAX_DECISIONS,
        selection: 'Newest decisions in the window; all metrics refer only to this selected sample.' },
      thresholds: { staleContextSeconds: STALE_CONTEXT_SECONDS, repeatedRejections: 3, limitedSellSample: 20 },
      summary, rejectionCounts,
      strategies: [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, group]) => ({
        strategyId: group[0].strategyId, strategyVersion: group[0].strategyVersion, ...summarize(group),
      })),
      findings,
      limitations: [
        'Deterministic diagnostics, not an AI judgment or automatic strategy update. No change is promoted by this report.',
        'Strategy labels are caller-reported; they do not prove which prompt, model, or configuration ran.',
        'PnL is assigned to the SELL decision; its inventory may have been acquired by another strategy or outside the window.',
        'Fees are already included in accounting PnL. Fees paid in this window need not equal the fees allocated to its sells.',
        'Manual prices, correlated/partial fills, open positions, and missing data prevent causal or reliable strategy ranking.',
        'Malformed requests and infrastructure failures are in server logs, not this decision sample.',
      ],
    };
  }
}
