# Evidence-based strategy review

The backend now records evidence and produces a deterministic review report. An external AI reviewer can use it to propose improvements to the signal flow or strategy. This is not model training, a built-in AI call, an optimizer, or automatic deployment. The backend does not run a scheduler. No API token is required in the token-free configuration.

## Trading flow

```text
Scheduled agent -> fetch market data -> GET /api/context
                -> reason using that context
                -> persist signal payload + Idempotency-Key in the scheduler
                -> POST /api/signals
Backend         -> validate -> deduplicate -> snapshot -> hard risk rules
                -> paper execution -> atomic accounting + decision + receipt
Reviewer        -> GET /api/review -> inspect decision evidence
                -> propose candidate -> tests -> separate paper evaluation
                -> evaluate against fixed criteria -> activate or discard
```

The optional [Codex VPS runner](CODEX_RUNNER.md) implements the first two lines as a separate process. The backend implements context, signal handling, evidence storage, and the report. The reviewer, candidate evaluation, activation, and rollback remain a documented workflow; there is no strategy registry or shadow runner yet.

## Scheduled signal contract

1. Fetch `GET /api/context?BTCUSDT=<price>&ETHUSDT=<price>` with prices from your market-data source. Each successful call stores the exact JSON response and returns a new UUID `contextId`. Use `/health` for availability polling to avoid unnecessary snapshots.
2. Generate a decision from that response. Send `strategyId`, `strategyVersion`, and `contextId` in the JSON payload. Strategy ID and version must be supplied together, each 1–64 characters: letters/digits first, then letters, digits, `.`, `_`, or `-`. Keep the prompt/configuration corresponding to each version in your strategy repository. These labels are caller-reported, not verified identities.
3. Before sending, save the complete payload and one unique `Idempotency-Key` in durable scheduler storage. For example, use a UUID, or `momentum-v1:2026-09-09T12:00:00Z:BTCUSDT` for exactly one decision in that slot. Keys are 1–128 characters and allow letters, digits, `:`, `.`, `_`, `-`, starting with a letter/digit. They are global to this database, not scoped to a strategy.
4. Submit that saved payload with the same key for every retry of this decision. Do not ask the model to regenerate the payload during a retry. A genuinely new decision needs a fresh context, reevaluation, and a new key.

Example request body (replace the context UUID with one returned by the backend):

```json
{
  "action": "BUY",
  "symbol": "BTCUSDT",
  "amountUsd": "5",
  "price": "100000",
  "confidence": 0.74,
  "riskLevel": "LOW",
  "rationale": "Example only; use the current strategy decision",
  "source": "scheduled-ai-trade-signal",
  "strategyId": "momentum",
  "strategyVersion": "v1",
  "contextId": "00000000-0000-4000-8000-000000000001"
}
```

Headers: `Content-Type: application/json` and `Idempotency-Key: <saved-key>`. No `Authorization` header is needed when `API_TOKEN` is blank.

| Outcome | Scheduler behavior |
| --- | --- |
| 200 executed/held | Save the decision ID and complete the run |
| 422 rejected | Save `decisionId` and `risk.code`; complete the run as a risk rejection |
| Timeout, 500, or a transient version conflict | Retry the saved payload/key with bounded backoff; investigate persistent errors |
| `Idempotency-Replayed: true` | Previously committed outcome returned; do not count it as another decision/fill |
| 409 `IDEMPOTENCY_CONFLICT` | Same key, different effective payload; investigate scheduler state instead of silently switching keys |
| 400 / 404 `CONTEXT_NOT_FOUND` | Correct the request or fetch valid context and reconsider the decision |

The same key and normalized payload return the original body and status, including HOLD and risk rejection, even after restart or later portfolio changes. That replay's portfolio is historical; fetch context for current state. Equivalent decimal inputs such as `5` and `"5.000000"` match. Changes to metadata, prices, strategy, or context also conflict. Receipts commit in the same SQLite transaction as the decision and financial writes. Failed transactions leave no receipt. Malformed requests are not recorded as decisions.

The new metadata and header are optional for existing clients. Without a key, each request is a new decision and can create another trade. Keys have no automatic expiration. Reset deletes receipts along with history: retire all pending scheduler requests before a deliberate reset or restore. Restoring an older database also restores its older deduplication state.

## Audit evidence

- `GET /api/decisions/:id` returns `{decision, agentContext}`. The decision includes the signal fields and `executionContext`; `agentContext` is the exact linked context payload, or null.
- `GET /api/contexts/:id` retrieves the original context without creating another snapshot.
- Execution snapshots contain the pre-execution portfolio, positions, quotes/marks, daily risk, hard limits, market-data mode, and risk result. Decimal values are strings to preserve accounting precision. HOLD has no mark lookup or order risk result; these fields are empty/null.
- Context response values retain the existing numeric API format. They record what the caller received, not unlimited decimal precision or proof of the model's reasoning. Rationale is caller-supplied audit text.
- Old decisions migrate as `default` / `unversioned`, with null context and execution snapshot. Historical evidence is not reconstructed or invented.

A linked context is not a reservation. It can become stale or refer to an earlier portfolio version. The report flags these cases; this release does not reject signals solely for context age/version. The backend always checks orders against its processing snapshot. The five-minute review threshold measures context age, not quote freshness.

## Generate a review

```sh
curl 'http://127.0.0.1:3000/api/review?days=7'
curl 'http://127.0.0.1:3000/api/review?days=7&strategyId=momentum&strategyVersion=v1'
pnpm review
pnpm review --days 14 --strategyId momentum --strategyVersion v1
```

`days` is a rolling UTC lookback, from 1 to 90, default 7. A version filter requires a strategy ID. HTTP review performs reads only. The CLI uses the configured database and normal startup migration/bootstrap, then writes a timestamped report and `data/reviews/latest.json`; it does not create paper decisions or change strategy settings. Run it from the project root with development dependencies installed. A production-only server can expose `/api/review` instead.

Reports include per-version summaries, rejection counts, fee totals, net realized PnL, diagnostic findings with up to five evidence decision IDs each, suggested changes, and required validation. Review fetches only the newest 1,000 decisions in the window; `window.truncated` explicitly marks a larger sample. All counts and metrics apply to the selected sample. Narrow the window or strategy filter if truncated; do not treat a partial report as full-window performance.

Diagnostics cover missing idempotency keys, missing strategy versions, missing audit/context, context older than 300 seconds, portfolio changes since context, three or more rejections of the same kind, and negative realized PnL. Fewer than 20 SELL fills produces a limited-sample note. These are transparent heuristics, not statistical confidence tests or proof that a proposed strategy will improve returns.

PnL is grouped by the strategy label on each SELL, even if the position was opened by another version or outside the review window. Partial fills are not independent completed trades. Fees are already accounted for in PnL; do not subtract the report's fee total again. The report does not calculate full equity returns, drawdown, market benchmarks, or hypothetical profits for rejected orders. Manual prices and open positions further limit comparisons.

## External review loop

Run the reviewer after collecting paper outcomes, independently of the trading cadence. Give it the report and access to decision details. A review should produce either `no_change` or one concrete candidate with:

1. Observed problem and supporting decision IDs, clearly separating facts from hypotheses.
2. Proposed prompt/configuration or code change and a new immutable strategy version.
3. A failing example or reproducible evaluation case before the change, plus expected behavior afterward.
4. Validation: risk/accounting checks, retry behavior, and evaluation against a separate paper dataset/time window with comparable initial state and costs.
5. Activation criteria and rollback criteria fixed before collecting candidate results.

Integration defects such as missing retry keys can be checked directly with failure/restart tests. A performance claim needs independent paper evaluation; a small profitable sample does not justify automatic activation. Use separate database files/processes for candidate and baseline portfolios if testing them together. Version labels alone do not isolate capital or positions.

For code changes, run `pnpm lint`, `pnpm typecheck`, `pnpm test`, and `pnpm smoke`. Keep hard limits, accounting invariants, and paper-only execution fixed. Publish a reviewable patch and evaluation evidence. Once a candidate meets the chosen criteria, change the external scheduler's pinned version; retain the previous prompt/configuration for rollback. Rolling back a strategy does not undo already recorded trades.

Use [REVIEWER_PROMPT.md](REVIEWER_PROMPT.md) as the external reviewer's starting prompt. The optional VPS runner invokes Codex CLI and provides timer templates; they take effect only when installed and enabled. The backend itself creates no recurring task and makes no model API calls.

## Storage and operations

Context snapshots, execution evidence, and receipts grow with requests. No automatic pruning is implemented because deleting receipts can make old retries execute again, and linked contexts support audit integrity. Monitor disk size on a small VPS, poll `/health`, back up SQLite consistently, and define a retention/export policy before high-frequency operation. Review bounds processing to 1,000 projected rows and does not load all execution JSON into memory.

Migration `0001` only adds tables, columns, and indexes. Back up an existing database before deployment, then run normal startup or `pnpm db:migrate`. Do not run `db:reset` to upgrade. No existing portfolio balance or trade is intentionally changed by the migration.
