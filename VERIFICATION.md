# Verification — 2026-09-09

Verified locally on Windows, Node.js 24.14.1, pnpm 11.13.1.

| Check | Result |
| --- | --- |
| `pnpm install --frozen-lockfile` | Passed |
| `pnpm peers check` | No peer dependency issues |
| `pnpm db:migrate` | Passed; default SQLite database initialized |
| `pnpm lint` | Passed, zero warnings |
| `pnpm typecheck` | Passed |
| `pnpm test` | **56 passed**, 4 test files |
| `pnpm build` | Passed |
| `pnpm smoke` | Passed: compiled server, real HTTP, process restart, direct SQLite checks |
| `pnpm db:reset` | Passed; development portfolio restored to $50 |
| `pnpm dev` | Started on localhost:3000; HTTP health/context passed; stopped after verification |

HTTP smoke covered health, initial context, BUY, HOLD, profitable SELL, oversized rejection, trade/decision history and restart persistence. SQLite `integrity_check` returned `ok`; `foreign_key_check` returned no violations.

Smoke database result:

- Cash: `46.993`; BTC quantity: `0.00004`; average entry: `100000`.
- Realized PnL: `0.997`; total fees: `0.007`.
- Two trades and four audited decisions (EXECUTED, HOLD, EXECUTED, REJECTED).
- Decimal accounting values stored as TEXT.

The detailed HTTP responses, SQL state and timestamps are in [data/latest-smoke.json](data/latest-smoke.json). Each smoke run retains its own database, report and server log under `data/smoke-*/`. These generated artifacts are gitignored.

The development database is separate: `data/paper-trader.sqlite`, with **cash/equity $50, no positions, no trades and no decisions** after verification. `.env` was copied from `.env.example` with localhost defaults.

No live exchange execution or external market-data calls were used. See [README.md](README.md) for accounting conventions, manual-price limitations and the roadmap.

## Deployment preparation

The README and deployment guide are in English. The exact install/build commands in `render.yaml` passed locally. The template also passed validation against Render's current JSON Schema (draft 2020-12); the generated result is in `data/render-validation.json`.

The template configures one Node service, a persistent SQLite disk, and token-free access. No cloud resources have been created, and the Linux/cloud deployment has not been exercised. Follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) to deploy and verify the assigned HTTPS endpoint.
