# AI Paper Trader MVP

A backend for an external AI agent managing a simulated **$50 portfolio**. The agent reads context and submits BUY, SELL, or HOLD signals. The backend owns validation, risk enforcement, accounting, and the audit trail. Only BTCUSDT and ETHUSDT spot paper trades are supported. There is no live exchange execution.

For reproducible historical strategy research, see the separate [quant backtest](quant/README.md) and [initial comparison results](docs/QUANT_BACKTEST.md). This research tool does not change the deployed strategy or submit signals.

For a separate forward paper portfolio that trades fixed rules against the AI's saved market inputs, see the [shadow control guide](docs/SHADOW_CONTROL.md). It uses its own backend/database and records action comparisons without changing the original AI strategy.

## Quick start

Requirements: **Node.js 24** and **pnpm 11**. The lockfile records tested dependency versions. `better-sqlite3` is a native dependency; building from source requires Python and C++ build tools (Visual Studio C++ Build Tools on Windows).

```sh
pnpm install --frozen-lockfile
pnpm dev
```

The API starts at `http://127.0.0.1:3000`. Startup applies migrations and creates the $50 portfolio only if it does not already exist. Configuration has defaults; optionally copy `.env.example` to `.env` using `cp .env.example .env` on Linux/macOS or `Copy-Item .env.example .env` in PowerShell.

```sh
pnpm lint
pnpm typecheck
pnpm test
pnpm smoke       # Build, real HTTP, process restart, and direct SQLite verification
pnpm db:migrate  # Apply migrations without starting HTTP
pnpm db:reset    # Clear paper history and restore cash to $50
pnpm review      # Save an evidence-based review report; default lookback is 7 days
pnpm build
pnpm start       # Run the compiled server
```

Stop the server and retire pending scheduler requests before `db:reset`. Reset clears positions, trades, decisions, quotes, context snapshots, and idempotency receipts in the configured database; it does not remove files or directories. Normal startup never resets the portfolio.

## Deployment

For an Ubuntu 24.04 x86_64 VPS, use the **[direct IP deployment guide](docs/VPS_UBUNTU.md)** and the templates in `deploy/ubuntu/`. The backend runs under systemd at `http://IP:3000` without an API token; SQLite remains in a separate persistent directory.

See the **[deployment guide](docs/DEPLOYMENT.md)** for Render setup, a self-hosted alternative, and post-deployment checks. The included [render.yaml](render.yaml) configures one Node service with a persistent disk and no API token. It is a deployment template; no cloud service has been created.

Run one writer process with `dist/`, `drizzle/`, installed dependencies, and persistent storage for `DATABASE_PATH`. Start from the project root. Fastify writes JSON logs and handles SIGINT/SIGTERM shutdown. Do not run multiple workers against this SQLite database.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Bind address; use `0.0.0.0` behind a hosting provider's proxy |
| `PORT` | `3000` | HTTP port; the host may supply its own value |
| `DATABASE_PATH` | `./data/paper-trader.sqlite` | SQLite file; use an absolute persistent path in production |
| `LOG_LEVEL` | `info` | Fastify log level |
| `API_TOKEN` | Empty | Optional bearer token; leave empty or omit for token-free access |

`API_TOKEN` is optional on every HOST. With an empty token, clients do not send an `Authorization` header. If a token is configured, API requests require `Authorization: Bearer <token>`; `/health` remains public.

## Connecting scheduled AI Trade Signal

For the VPS with a ChatGPT-authenticated Codex CLI, use the included **[Codex runner](docs/CODEX_RUNNER.md)**. It fetches public market data, validates a structured decision, persists retries, and includes a systemd timer for every hour at minute 02. Start with a dry run; installing the backend alone does not activate the runner.

The local `.env` and Render template use token-free access. For each scheduled run:

1. Obtain market prices and call `GET /api/context?BTCUSDT=<btc-price>&ETHUSDT=<eth-price>` for portfolio state and risk budget.
2. Produce one BUY, SELL, or HOLD decision. BUY/SELL must include the decision's execution price in `price`. Include the returned `contextId` and a paired `strategyId` / `strategyVersion`.
3. Persist that payload and a unique `Idempotency-Key` in the scheduler before sending JSON to `POST /api/signals`. Send the key as a header alongside `Content-Type: application/json`. Use `source: "scheduled-ai-trade-signal"` for audit attribution.
4. Inspect `status` and, when rejected, `risk.code`. HTTP 422 is an audited risk outcome. Retry an ambiguous timeout using the exact saved payload and key; the backend returns the original result without a second fill. Different payloads using the same key return 409 `IDEMPOTENCY_CONFLICT`.

See [the self-review guide](docs/SELF_REVIEW.md) for the full retry contract, audit endpoints, review report, and candidate evaluation workflow. Metadata and keys are optional for existing clients; omitted keys provide no deduplication. This release provides review evidence and suggestions, not automatic strategy activation.

A job on the same machine can use `http://127.0.0.1:3000`. A remote job needs the backend's reachable address, such as its deployed HTTPS URL; the job's localhost refers to its own machine. Without a token, any client that can reach the backend can submit paper signals. Schedule creation and actual job connection are configured in the external scheduler.

## Architecture

```text
src/app/routes/       Zod validation -> service -> HTTP/JSON
src/services/         Trading, context, history, and review orchestration
src/review/           Shared HTTP/CLI review options
src/domain/trading/   Entities, cost basis, PnL, and pure valuation
src/risk/             Hard limits, risk engine, and UTC daily-risk computation
src/execution/        TradeExecutor interface and PaperTradeExecutor
src/market/           MarketDataProvider interface and manual adapter
src/repositories/     Persistence contract and SQLite implementation
src/db/               Drizzle schema, connection, migrations, and bootstrap
src/config/           Environment validation
src/shared/           Decimal helpers, errors, and signal queue
tests/                Risk, API, persistence, failure, and concurrency tests
scripts/              Migrate, reset, review, and smoke-test commands
runner/               Optional Linux Python/Codex signal runner and failure tests
```

Routes contain no business logic. `buildApp()` is the composition root and supports clock, executor, and market-data injection. The repository provides a shared write boundary so portfolio, position, trade, decision, and quote updates are atomic. Domain code does not depend on Fastify, Zod, or SQLite.

## API examples

These examples use a POSIX shell. On Windows, use `Invoke-RestMethod` or adjust JSON quoting for `curl.exe`. Replace localhost with your deployment URL for remote requests.

```sh
curl http://127.0.0.1:3000/health
curl 'http://127.0.0.1:3000/api/context?BTCUSDT=100000&ETHUSDT=2000'

curl -X POST http://127.0.0.1:3000/api/signals \
  -H 'Content-Type: application/json' \
  -d '{"action":"BUY","symbol":"BTCUSDT","amountUsd":5,"price":100000,"confidence":0.74,"riskLevel":"LOW","rationale":"Momentum remains positive","source":"scheduled-ai-trade-signal"}'

curl -X POST http://127.0.0.1:3000/api/signals \
  -H 'Content-Type: application/json' \
  -d '{"action":"HOLD","confidence":0.5,"riskLevel":"LOW","rationale":"Wait for confirmation","source":"scheduled-ai-trade-signal"}'

curl -X POST http://127.0.0.1:3000/api/signals \
  -H 'Content-Type: application/json' \
  -d '{"action":"SELL","symbol":"BTCUSDT","amountUsd":2,"price":200000,"confidence":0.8,"riskLevel":"LOW","rationale":"Take partial profit","source":"scheduled-ai-trade-signal"}'

curl 'http://127.0.0.1:3000/api/trades?limit=20&symbol=BTCUSDT'
curl 'http://127.0.0.1:3000/api/decisions?limit=20'
```

| Endpoint or outcome | Response |
| --- | --- |
| `GET /health` | `200 {"status":"ok"}` |
| `GET /api/context` | A new persisted `contextId`, portfolio, positions, net PnL, UTC risk budget, quote source/asOf, and 20 recent trades |
| `POST /api/signals` BUY/SELL | `200 {status:"executed", decisionId, trade, portfolio}` |
| `POST /api/signals` HOLD | `200 {status:"held", decisionId}`; records a decision only |
| Risk rejection | `422 {status:"rejected", decisionId, risk:{code,reason}}`; records a rejected decision |
| `GET /api/trades` | `{trades:[...]}`, newest first; optional `symbol`, `limit` defaults to 50 and is capped at 200 |
| `GET /api/decisions` | `{decisions:[...]}`, newest first; `limit` defaults to 50 and is capped at 200 |
| `GET /api/decisions/:id` | `{decision, agentContext}` with execution evidence and the linked original context |
| `GET /api/contexts/:id` | The exact previously stored context response |
| `GET /api/review` | Read-only diagnostics, evidence IDs, and per-strategy summaries; `days=1..90`, default 7; optional strategy filters |

Invalid input returns 400; missing/invalid authentication when enabled returns 401; a stale concurrent commit returns 409; internal failures return 500 without exposing implementation details. Malformed inputs are not agent decisions. Infrastructure failures are logged; risk rejections are audited. Strict schemas reject unknown fields, including leverage or policy overrides. Unsupported BUY/SELL symbols produce an audited `INVALID_SYMBOL` rejection.

Metadata: `confidence` is in [0,1], `riskLevel` is LOW/MEDIUM/HIGH, `rationale` is 1-2000 characters, and `source` is 1-100 characters. Optional `strategyId` and `strategyVersion` must be supplied together; omission is recorded as `default` / `unversioned`. Optional `contextId` must identify a stored context, otherwise the request returns 404. Metadata cannot change hard rules. HOLD does not require symbol, amount, or price; supplied fields are still validated.

## Hard risk rules

- Initial capital is $50; each BUY or SELL has a maximum $5 notional.
- Total marked exposure after BUY cannot exceed $20. SELL may reduce exposure even when price appreciation has pushed it above $20.
- Cash must cover BUY notional plus the 0.1% fee. No leverage, shorting, negative cash, or negative positions.
- SELL uses `amountUsd / price` and cannot exceed the quantity held.
- Daily realized PnL is **net of fees**, aggregated over the current UTC day. At or below -$3, both BUY and SELL are blocked; HOLD remains available. Same-day profits offset losses.
- The daily limit is checked **before** an order. A SELL can cross the threshold and block subsequent orders. A new UTC day restores the daily budget without resetting lifetime PnL.

Limits are backend constants in `src/risk/limits.ts`, not environment or signal settings. The risk engine returns a `RiskDecision` discriminated union; rejected orders never reach the executor.

## Prices and accounting

The MVP uses manual prices and makes no exchange calls. A signal's price determines its paper fill and marks that symbol during risk checks. Other symbols use their last successful fill price stored in SQLite. HOLD and rejected signals do not update quotes. Context query prices are temporary valuation marks: **they do not update portfolio state, stored market prices, or subsequent valuations**. The response itself is saved as audit evidence under its `contextId`. Quotes include source/asOf; there is no freshness gate yet.

`MarketDataProvider.getPrice()` returns `Promise<Amount>` (Decimal). An injected provider supplies context valuation and marks for existing holdings; context query overrides apply only to the manual adapter. Paper fills still use the signal price. Freshness and price validation should be designed before using a live market feed.

Money and quantity use an isolated `decimal.js` constructor with 48-digit precision and are stored as **decimal TEXT**. Accounting does not use floating-point SQL sums. USD/price inputs accept JSON numbers or exact decimal strings between 0.000001 and 1,000,000,000, with at most six decimal places. The $5 risk cap is applied after validation.

Quantity is notional / price, rounded down to 24 decimal places; notional and fees follow the requested order. Quantity rounding is less than 10^-24 asset per fill, equivalent to less than 10^-15 USD at the maximum input price. Partial sales can leave rounding dust; there is no sweep endpoint. Responses convert Decimal to JSON numbers for agents, so display precision is limited by JavaScript numbers. Persisted decimal values remain the accounting source of truth.

- BUY $5 at $100000: quantity 0.00005, fee 0.005, cash debit **5.005**. `grossUsd=5`, `netUsd=5.005`.
- Average entry is total gross cost basis / quantity. Multiple buys are quantity-weighted.
- SELL: `netUsd = grossUsd - feeUsd` is the cash credit. Cost basis and BUY fees are released in proportion to the quantity sold.
- `realizedPnl = net proceeds - allocated gross cost - allocated BUY fees`.
- `unrealizedPnl = market value - remaining gross cost - remaining BUY fees`.
- `equity = cash + positionsValue`; `equity - initialCapital` equals realized plus unrealized PnL within the chosen precision. Fees are already included: **do not subtract totalFees again**.

Immediately after a BUY at an unchanged price, unrealized PnL is -0.005 and realized PnL is 0. BUY $5 at $100000, then SELL $2 at $200000: realized PnL is **0.997**, cash is **46.993**, and remaining quantity is **0.00004**. Full liquidation removes the position; a later BUY starts a new cost basis.

## Database and consistency

The default database is `data/paper-trader.sqlite`. There are seven application tables: singleton `portfolio` (id=1), `positions` (symbol primary key), `trades`, `agent_decisions`, `market_prices`, `context_snapshots`, and `signal_receipts`. Executed decisions reference their trades; contexts and receipts link audit evidence. A sequence orders history even when timestamps match. Timestamps are ISO UTC.

Drizzle SQL migrations live in `drizzle/`; startup and `db:migrate` apply only pending migrations. After schema changes, run `pnpm db:generate`, review the generated SQL, and migrate. WAL, foreign keys, busy timeout, and synchronous FULL are enabled.

One process serializes signals. The paper executor has no side effects; after risk approval, accounting and audit changes commit in a **BEGIN IMMEDIATE transaction**. Portfolio version checks prevent stale overwrites. Failed writes roll back. Context uses a consistent snapshot and cannot observe partially written state. Do not run clustered workers against the same file.

With an `Idempotency-Key`, receipts commit atomically with accounting and audit writes. Replays return the original response and status with `Idempotency-Replayed: true`; a different effective payload returns 409. Keys do not expire automatically. Without a key, resubmitting a valid signal can create another trade. A live executor is not a drop-in production upgrade: it requires external order lifecycle, reconciliation, and partial-fill handling.

Migration `0001` preserves existing trades and balances. Older decisions retain null execution/context evidence. Audit snapshots and receipts grow over time; there is no automatic pruning. Use `/health` for frequent uptime checks and monitor disk usage on a small VPS.

## Testing and verification

Vitest covers BUY, weighted average entry, profitable/losing SELL, full liquidation, fees, HOLD, every risk code, decimal strings, UTC rollover, validation, history, concurrent requests, restart, database constraints, audit/receipt rollback, executor failure, version conflicts, reset, auth/provider injection, durable retries, old-schema migration, context evidence, and read-only bounded review reports.

`pnpm smoke` creates a separate `data/smoke-*/paper.sqlite`, starts the **compiled server in another process**, sends real HTTP requests, restarts it, and reads SQLite directly. Expected final state: cash **46.993**, quantity **0.00004**, realized PnL **0.997**, fees **0.007**, and **2 trades / 4 decisions**, with valid integrity and foreign keys. The latest report is `data/latest-smoke.json`; each run retains its report and server log. Smoke tests do not change the development portfolio. See [VERIFICATION.md](VERIFICATION.md) for recorded local results.

## Roadmap (not implemented)

1. Price freshness enforcement, then Binance/Bybit market-data adapters.
2. External scheduled agent connection, strategy registry, and isolated candidate evaluation/activation.
3. PnL dashboard, AI versus BTC buy-and-hold benchmark, and WebSocket prices.
4. Multiple portfolios when needed.
5. Live execution as a separate phase after order lifecycle and reconciliation design.

References: [Fastify validation](https://fastify.dev/docs/latest/Reference/Validation-and-Serialization/), [Drizzle SQLite](https://orm.drizzle.team/docs/sqlite/get-started-sqlite), and [Drizzle transactions](https://orm.drizzle.team/docs/transactions).
