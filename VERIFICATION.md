# Verification — 2026-09-09

Verified locally on Windows, Node.js 24.14.1, pnpm 11.13.1.

| Check | Result |
| --- | --- |
| `pnpm install --frozen-lockfile` | Passed |
| `pnpm peers check` | No peer dependency issues |
| `pnpm db:migrate` | Initial setup passed; new migration also exercised by startup on legacy fixtures and smoke databases |
| `pnpm lint` | Passed, zero warnings |
| `pnpm typecheck` | Passed |
| `pnpm test` | **79 passed**, 5 test files |
| `pnpm build` | Passed |
| `pnpm smoke` | Passed: compiled server, real HTTP, process restart, direct SQLite checks |
| `pnpm review --days 7 --strategyId smoke --strategyVersion v1` | Passed on the smoke database; saved 4-decision report with PnL `0.997` and fees `0.007` |
| `pnpm db:reset` | Passed on isolated test databases, including clearing linked context and receipts; development reset was only part of initial MVP verification |
| `pnpm dev` | Initial MVP verification passed; latest revision exercised through the compiled server smoke test |

HTTP smoke covered health, initial context, BUY, HOLD, profitable SELL, oversized rejection, trade/decision history, linked context retrieval, decision detail, and restart persistence. Retrying the original BUY after restart returned the original response without another fill; a changed payload using its key returned 409. The review API reported the expected strategy metrics. SQLite `integrity_check` returned `ok`; `foreign_key_check` returned no violations.

Additional automated checks cover simultaneous retries, a competing writer's winning receipt, receipt-insert rollback for BUY/HOLD/rejection, metadata validation, migration from the previous schema, exact context evidence, stale/changed context findings, strategy/time filters, bounded review truncation, and a database-content comparison proving the review endpoint does not write state.

Smoke database result:

- Cash: `46.993`; BTC quantity: `0.00004`; average entry: `100000`.
- Realized PnL: `0.997`; total fees: `0.007`.
- Two trades and four audited decisions (EXECUTED, HOLD, EXECUTED, REJECTED).
- Four execution snapshots and one durable BUY receipt. The other three smoke decisions intentionally use the legacy unkeyed/unlinked contract, which the review flags.
- Decimal accounting values stored as TEXT.

The detailed HTTP responses, SQL state and timestamps are in [data/latest-smoke.json](data/latest-smoke.json). Each smoke run retains its own database, report and server log under `data/smoke-*/`. These generated artifacts are gitignored.

The development database is separate: `data/paper-trader.sqlite`, with **cash $50, no trades and no decisions**, unchanged by this audit/review verification. It still has the initial migration; the next normal startup or `pnpm db:migrate` will apply `0001`. No reset is needed. `.env` uses the token-free configuration.

No live exchange execution or external market-data calls were used. See [README.md](README.md) for accounting conventions, manual-price limitations and the roadmap.

## Deployment preparation

The README and deployment guide are in English. The exact install/build commands in `render.yaml` passed locally. The template also passed validation against Render's current JSON Schema (draft 2020-12); the generated result is in `data/render-validation.json`.

The template configures one Node service, a persistent SQLite disk, and token-free access. No cloud resources have been created, and the Linux/cloud deployment has not been exercised. Follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) to deploy and verify the assigned HTTPS endpoint.
