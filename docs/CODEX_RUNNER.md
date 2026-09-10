# Codex CLI paper-signal runner on the VPS

The optional Linux runner fetches public Binance data, gets a marked portfolio
context, invokes Codex CLI with the dedicated user's ChatGPT login, validates its
JSON decision, and submits at most one paper signal per UTC hourly slot. No
`API_TOKEN`, Binance API key, or OpenAI API key is needed for this setup. Codex
runs consume the account's Codex allowance; they are not unlimited.

This runs independently of the earlier ChatGPT “AI Trade Signal” task. That task
has not been changed or connected by these files. Keep only one signal producer
submitting to this portfolio.

## Prerequisites already verified on this VPS

- Backend at `http://127.0.0.1:3000`, managed by `ai-paper-trader.service`.
- Source at `/opt/ai-paper-trader`, owned by root; database owned by `paper-trader`.
  The runner user does not write either location.
- User `trade-agent`, home `/home/trade-agent`, shell `/bin/bash`.
- Global `codex`; `runuser -l trade-agent -c 'codex login status'` reports ChatGPT
  login. Keep this dedicated user's Codex configuration free of unrelated MCP
  servers, plugins, and hooks.
- Bubblewrap works with the Ubuntu per-application AppArmor profile. No global
  user-namespace restriction was disabled.
- Python 3.12 is available as `python3`; `python` is unnecessary.
- An actual HOLD/replay test passed with cash unchanged at $50.
- Actual BTC/ETH price and hourly candle GETs succeeded from the VPS.

Codex uses `exec --sandbox read-only --output-schema ... -o ...`, saved ChatGPT
authentication, and a fresh invocation each time. Shell execution and web search
are disabled in the invocation. All data is supplied in the prompt; Codex returns
JSON and the Python wrapper handles API requests and persistence.
[OpenAI non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

## 1. Pull the published code and perform a dry run

Commit and push the local changes to GitHub first. A VPS `git pull` cannot retrieve
uncommitted local files. On the VPS, as root:

```bash
(
  set -e
  cd /opt/ai-paper-trader
  git pull --ff-only
  test -f runner/run.py
  install -d -o trade-agent -g trade-agent -m 0700 /home/trade-agent/paper-runner
  runuser -l trade-agent -c 'python3 -B /opt/ai-paper-trader/runner/run.py --dry-run'
)
```

The runner uses Python's standard library. Adding only runner files does not
require a Node rebuild or backend restart. A dry run creates context snapshots
and local evidence, but **never POSTs a signal** and does not consume the live
hourly slot. Look for `"status":"dry-run"` and `"submitted":false`, then inspect
the payload and rationale. Do not submit a saved dry-run payload later: an actual
run must obtain new data and context.

Artifacts are under `/home/trade-agent/paper-runner/state/runs/<UTC-time>-<UUID>/`:
input snapshot, full prompt, schema, Codex logs/output, and validated payload.
The earlier `smoke-request.json` is separate and never used by this runner.

## 2. Run one actual paper decision

After the dry run succeeds:

```bash
runuser -l trade-agent -c 'python3 -B /opt/ai-paper-trader/runner/run.py'
curl --fail-with-body 'http://127.0.0.1:3000/api/decisions?limit=3'
curl --fail-with-body 'http://127.0.0.1:3000/api/trades?limit=3'
```

This can submit BUY, SELL, or HOLD to the simulated portfolio. HTTP 200
`executed`/`held` and HTTP 422 `rejected` are completed outcomes. The result prints
the decision ID and replay status. Repeating a completed UTC hourly slot returns
`skipped`, including after process restarts.

## 3. Enable the hourly timer

After one actual run completes, install the units as root:

```bash
(
  set -e
  cd /opt/ai-paper-trader
  install -m 0644 deploy/ubuntu/ai-paper-trader-agent.service /etc/systemd/system/
  install -m 0644 deploy/ubuntu/ai-paper-trader-agent.timer /etc/systemd/system/
  systemd-analyze verify /etc/systemd/system/ai-paper-trader-agent.service /etc/systemd/system/ai-paper-trader-agent.timer
  systemctl daemon-reload
  systemctl enable --now ai-paper-trader-agent.timer
)
systemctl list-timers ai-paper-trader-agent.timer --no-pager
```

Schedule: **00:02, 01:02, ... 23:02 UTC**, two minutes after every UTC hour
(24 scheduled analyses per day). Indicators still use closed **1h candles**.
The existing AI decision policy runs each hour; this schedule change does not
implement a new trading strategy or switch AI to a daily review.
The timer does not catch up missed runs after downtime or immediately submit on
enable. A manually completed slot is skipped. Systemd serializes service
activations, and an OS lock prevents manual/scheduled overlap using the same
state directory. Always use the same directory for this portfolio.

### Upgrading an existing two-hour runner

Publish these code changes before pulling them on the VPS. Stop future timer
activations with `systemctl stop ai-paper-trader-agent.timer`, then check
`systemctl is-active ai-paper-trader-agent.service`. If it reports `activating`
or `active`, let that run finish before updating any files. Pull the updated
runner, prompt, and timer together, then repeat the unit installation and
`daemon-reload` / `enable --now` commands above. Confirm the next trigger is at
minute 02 with `systemctl list-timers ai-paper-trader-agent.timer --no-pager`.
The backend does not need a rebuild or restart for this change.

Keep the existing state directory. New requests record `slotSeconds: 3600`.
Completed legacy requests without that field retain their original two-hour
window: the first hourly activation may therefore be skipped until that window
ends. Pending legacy requests are recovered with their exact original body,
key, and expiry before any new analysis; expired ambiguous outcomes still block
the runner. Do not delete state to force an extra run. Keep the hourly runner
when rolling back a strategy; older runner code cannot interpret hourly state.

```bash
systemctl status ai-paper-trader-agent.timer --no-pager
journalctl -u ai-paper-trader-agent.service -n 80 --no-pager
cat /home/trade-agent/paper-runner/state/state.json
curl --fail-with-body 'http://127.0.0.1:3000/api/review?days=7'
```

The oneshot service normally becomes `inactive (dead)` after success; the timer
stays active. Inspect saved outcomes and logs. No email/chat alerts are configured.

Pause future runs with:

```bash
systemctl disable --now ai-paper-trader-agent.timer
```

This does not cancel an active service. Let it finish before an upgrade. For an
emergency, `systemctl stop ai-paper-trader-agent.service` stops its process group,
but an in-flight POST may already have committed. Inspect pending state and
backend evidence before starting again.

## Decision policy and safeguards

`runner/strategy.md` defines a **baseline paper experiment**, not a backtested or
validated profitable strategy. It considers trend, fees, holdings, and uncertainty
and prefers HOLD when evidence is insufficient. BUY requires price > SMA20 > SMA50
and positive 24-hour closed-candle return. BUY/SELL requires reported confidence
of at least 0.70 and risk below HIGH. Confidence is not a calibrated probability.
At most one symbol is traded per run.

The runner fetches 101 hourly candles and uses 100 fully closed candles, rejecting
gaps, duplicates, invalid OHLCV, and a stale latest closed candle. SMA and 24-hour
return use closed candles. The wrapper supplies execution prices from public
spot ticker observations, never the model. Quote receipt time establishes
collection time, not proof of an exchange fill. The backend still uses a manual
paper-price model without spread, depth, or slippage.
[Binance public data endpoints](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md)

Malformed output is rejected. Preflight checks cover cash including fees, marked
exposure, held quantity, and daily-loss status. Invalid orders are not silently
resized or converted into an invented HOLD. The backend is the final authority
for exact accounting and fixed risk limits. Context is not a reservation: another
client can change the portfolio after the runner's final version check.

Snapshots expire after 240 seconds, with a 15-second margin before each POST.
Pending requests also expire at the next hourly slot boundary, including UTC midnight.
Codex has a 180-second timeout. A UTC risk-day or hourly-slot change during
analysis aborts submission. A timeout, quota failure, stale snapshot, or changed
portfolio fails the run before creating a new pending request.
The market server's candle hour must also match the schedule slot before
analysis, preventing a clock-boundary mismatch from reusing the previous candle.

Strategy labels are `codex-trend` and `v1-<hash>`; the hash covers prompt, schema,
and runner source. Exact input/output and CLI logs are retained. The default model
comes from the dedicated user's Codex configuration and may change independently
of that hash; inspect CLI logs when comparing results. There is no automatic
prompt rewrite, strategy activation, risk-limit change, or model training.
See [SELF_REVIEW.md](SELF_REVIEW.md).

## Retry and failure recovery

Before the first POST, the wrapper atomically writes and fsyncs the **exact body
and UUID key** in `state.json` and the run's `request.json`. Uncertain responses
and HTTP 5xx/429 receive at most three attempts with bounded backoff using the
same bytes/key. A later invocation resumes pending work before new data or model
calls. HTTP 409 and other non-transient errors retain the request for investigation.

An expired pending request blocks automatic replay. Idempotency alone cannot
prove whether its first attempt committed: an unreceived old order could execute
at an obsolete price if replayed. Do not delete `state.json`, edit its body, or
invent a new key to bypass the block. Pause the timer and inspect saved request,
backend decision/context history, and journal. Verify the matching committed
decision or establish that the old request cannot still execute before retiring
it. This deliberately favors avoiding stale/duplicate fills over availability.

Keep state and database backups together. A reset or old database restore can
erase receipts; reconcile/retire old requests first. Monitor disk usage for SQLite
and runner evidence. No automatic retention deletion is configured.

## Developer checks

On Linux with Python 3.12:

```bash
python3 -B -m unittest discover -s runner -p 'test_runner.py' -v
```

Tests use fixtures and a local HTTP server, never Codex, Binance, or the deployed
portfolio. They cover lost responses after commit, recovery, exact retries, stale
pending requests, hourly slot deduplication, legacy two-hour state upgrades,
clock-boundary mismatches, locks, dry runs, context changes, candle
quality, and order boundaries. Backend tests separately cover persistent receipts
and accounting.
