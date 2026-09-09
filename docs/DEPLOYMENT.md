# Deployment

Deploy this backend as **one long-running Node process with persistent disk storage**. SQLite is a local database file. The repository includes a Render deployment template; it has not been deployed to a cloud account.

## Recommended setup: Render

Use a paid Node web service with a persistent disk. Render's ordinary filesystem is ephemeral; only files under the disk mount survive redeploys and restarts. Disk-backed services have a brief interruption during deployment. [Persistent disk documentation](https://render.com/docs/disks)

### Deploy from the included Blueprint

1. Push this project to a GitHub or GitLab repository, including `render.yaml`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `src/`, and `drizzle/`. Keep `.env`, `node_modules/`, `dist/`, and local SQLite files out of Git.
2. In Render, choose **New > Blueprint**, connect the repository, and use its root `render.yaml`.
3. Review the service and disk charges before applying. The template selects one `0.5c-512mb` instance, Singapore, and a 1 GB disk. Change the region before creation if needed.
4. Apply the Blueprint and wait for the build and health check to complete.
5. Copy the service's HTTPS URL into your scheduled AI Trade Signal configuration.

The template disables automatic deploys; deploy later changes manually after checking them. No `API_TOKEN` variable is needed. [Blueprint reference](https://render.com/docs/blueprint-spec)

### Equivalent manual service settings

If you prefer creating a Web Service manually, use these exact project settings:

| Setting | Value |
| --- | --- |
| Runtime | Node |
| Root directory | Repository root |
| Build command | `npx --yes pnpm@11.13.1 install --frozen-lockfile --prod=false && npx --yes pnpm@11.13.1 build` |
| Start command | `node dist/app/server.js` |
| Health check | `/health` |
| Instances | 1 |
| Disk mount | `/var/data` |
| Disk size | 1 GB initially |

Set these environment variables in the service dashboard:

```dotenv
NODE_VERSION=24.14.1
NODE_ENV=production
HOST=0.0.0.0
DATABASE_PATH=/var/data/paper-trader.sqlite
LOG_LEVEL=info
```

Omit `API_TOKEN` or leave it empty. Let Render supply `PORT`; the application reads it from the environment. Binding to `0.0.0.0` makes the service reachable through Render's proxy. [Web service port binding](https://render.com/docs/web-services#port-binding)

`NODE_VERSION` pins the runtime to the version tested locally. The build command selects pnpm explicitly and installs dev dependencies for TypeScript compilation, even with `NODE_ENV=production`. Render's native runtime includes the C++/Python toolchain needed by native dependencies such as `better-sqlite3`. Build dependencies on the host instead of uploading Windows `node_modules`. [Node version configuration](https://render.com/docs/node-version), [native runtime tools](https://render.com/docs/native-runtimes)

Startup applies pending Drizzle migrations and creates the initial $50 portfolio on the mounted disk. Leave build/pre-deploy database commands unset: Render's persistent disk is available only at runtime. Do not put `db:reset` in any deployment command. [Disk availability](https://render.com/docs/disks#disk-limitations-and-considerations)

## Verify the deployed service

Replace the example URL with the address assigned to your service. The following commands use a POSIX shell:

```sh
BASE_URL='https://your-service.onrender.com'
curl --fail-with-body "$BASE_URL/health"
curl --fail-with-body "$BASE_URL/api/context"
```

On a new database, health returns `{"status":"ok"}`, cash/equity are $50, and positions are empty. Existing deployments preserve their current portfolio.

Send a HOLD to verify signal delivery without creating a trade:

```sh
curl --fail-with-body -X POST "$BASE_URL/api/signals" \
  -H 'Content-Type: application/json' \
  -d '{"action":"HOLD","confidence":0.5,"riskLevel":"LOW","rationale":"Deployment connectivity check","source":"deployment-check"}'
curl --fail-with-body "$BASE_URL/api/decisions?limit=1"
```

The response should be `status: "held"`, and the decision should appear in history. This creates one audit entry. Restart or redeploy the service, then query decisions again: the same entry must remain. A missing entry indicates that the configured database is not on the persistent disk.

For PowerShell, the equivalent connectivity checks are:

```powershell
$baseUrl = 'https://your-service.onrender.com'
Invoke-RestMethod "$baseUrl/health"
Invoke-RestMethod "$baseUrl/api/context"
$signal = @{
  action = 'HOLD'
  confidence = 0.5
  riskLevel = 'LOW'
  rationale = 'Deployment connectivity check'
  source = 'deployment-check'
} | ConvertTo-Json
Invoke-RestMethod "$baseUrl/api/signals" -Method Post -ContentType 'application/json' -Body $signal
```

## Connect the scheduled agent

Configure the external scheduled AI Trade Signal job with:

```text
Base URL: https://your-service.onrender.com
Context:  GET /api/context?BTCUSDT=<current-price>&ETHUSDT=<current-price>
Signal:   POST /api/signals
Headers:  Content-Type: application/json, Idempotency-Key: <saved-decision-key>
Auth:     None
Source:   scheduled-ai-trade-signal
```

Use prices supplied by your market-data source, not the illustrative prices in the README. Query prices affect that context valuation only; BUY/SELL must include their own `price`. Include the returned `contextId` and paired `strategyId` / `strategyVersion`. Persist the payload and key before submission, then retry uncertain POST outcomes using that same saved payload/key. Handle HTTP 422 as a recorded risk rejection and 409 `IDEMPOTENCY_CONFLICT` as a scheduler inconsistency. See [the self-review guide](SELF_REVIEW.md) for evidence retrieval and the separate review workflow.

The backend does not run the scheduler or contact ChatGPT itself. It must be reachable from the scheduler's runtime. With authentication disabled, other clients that can reach the URL can also submit paper decisions.

## Self-hosted alternative

On an existing Linux server, install Node.js 24, pnpm 11, and the native build toolchain. Clone the project into a directory owned by the application user and run:

```sh
pnpm install --frozen-lockfile --prod=false
pnpm build
```

Configure the process environment with an absolute database path in a writable persistent directory, for example:

```dotenv
NODE_ENV=production
HOST=127.0.0.1
PORT=3000
DATABASE_PATH=/var/lib/ai-paper-trader/paper.sqlite
LOG_LEVEL=info
API_TOKEN=
```

Create that data directory and give the application user write access. Run `node dist/app/server.js` from the project root under the server's process supervisor with restart-on-failure. Configure an HTTPS reverse proxy to `127.0.0.1:3000` and point the scheduler at the resulting URL. Keep exactly one application process. If the scheduler runs on the same machine, it can call localhost directly.

This alternative requires your own domain/proxy and service management; the Render template handles the hosted service configuration above.

## Updates and data operations

- Run local checks before publishing changes: `pnpm lint`, `pnpm typecheck`, `pnpm test`, and `pnpm smoke`.
- Keep the `drizzle/` directory in deployments; compiled startup still reads its migration files.
- Back up SQLite before schema changes. Use a SQLite-aware backup, such as `better-sqlite3`'s backup API, instead of copying only an active WAL-mode database file. Keep a copy outside the service disk. [SQLite backup API](https://github.com/WiseLibs/better-sqlite3/blob/master/docs/api.md#backupdestination-options---promise)
- For recovery, pause the scheduler and stop the writer before replacing the database with a consistent backup. Match the code version to the restored schema, then verify health, context, and history before resuming signals.
- To intentionally reset paper state, pause the scheduler and stop the writer, then run `pnpm db:reset` in an environment that can access the actual database. A host's build environment may not share the runtime disk.
- A new cloud disk starts at $50. Local development data is not uploaded by this template.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Host cannot detect the HTTP port | Set `HOST=0.0.0.0` and allow the provided `PORT` |
| Portfolio starts over after redeploy | Check disk mount and `DATABASE_PATH`; the file must be inside `/var/data` |
| Missing migration files | Ensure `drizzle/` is committed and deployed alongside `dist/` |
| `tsc` not found in the build | Install with `--prod=false`; compilation needs dev dependencies |
| Native SQLite binding error | Use Node 24 and reinstall dependencies on the target operating system |
| HTTP 401 | Remove any configured `API_TOKEN` for the intended token-free setup |
| HTTP 422 | Inspect `risk.code`; the backend has rejected and audited the signal |
| Scheduler cannot connect | Use the deployed HTTPS URL, not the scheduler's localhost |

Local build and HTTP smoke results are recorded in [VERIFICATION.md](../VERIFICATION.md). Local checks do not establish that a cloud deployment has succeeded; use the deployed checks above after creating the service.
