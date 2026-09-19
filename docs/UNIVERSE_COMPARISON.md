# Two versus five coins: isolated forward paper experiment

The September 19, 2026 experiment compares BTC/ETH against
BTC/ETH/SOL/BNB/XRP. Both accounts start with $50 cash at the same future hourly
slot, share an immutable five-coin price event, and use the existing trend entry
and exit rules. Neither branch invokes AI: this isolates the asset universe.
The original V2 AI/control experiment continues independently without a reset
or source change on its existing deployment.

Each entry remains $5, with no adding, at most one action per account per hour,
and a $20 marked exposure limit. Exits take priority. Entry and exit ties use
BTC, ETH, SOL, BNB, XRP order, restricted to the branch's universe. Five watched
coins do not mean five simultaneous $5 positions. Reducing sells remain available
after a daily/equity loss gate; the $47 equity floor is unchanged and is not a
guaranteed maximum loss.

Paper fills include 0.1% fees on each side. Reporting includes net equity,
buys by coin, filled turnover, sampled drawdown, mean hourly exposure, missing
slots and pending requests. Extra 5/10 bps execution costs are reported as
first-order costs on actual filled turnover, not as a full slippage replay.
Prices are cached quotes, not executable order-book fills. More investment
exposure can account for higher returns; compare exposure and downside too.
Do not promote automatically or mix these accounts with the older V2 balances.

## Verified installation, September 19, 2026

At 17:35 UTC+7, both new backends and all three new timers were active. Both
ledgers reconciled with $50 cash and zero trades. The shared first scheduled
snapshot is **18:02 UTC+7**, with decisions beginning at 18:02:15.
The comparison version is `v2-0bc9367e1dbe4e56`; the existing deployment still
matches its original `v2-5cb4bb2b37123eda` manifest and all its timers are active.

Validation passed: 87 backend tests, TypeScript and lint checks, 51 Linux Python
runner tests, standard HTTP/restart smoke, original paired smoke and the new
two/five-universe smoke. The latter verified a shared synthetic input causing
two-coin HOLD versus five-coin SOL BUY, zero AI calls, no duplicate fill after
restart/retry, and matching ledgers/reports. These are software checks, not
evidence of investment performance.

Deployment evidence is saved at
`/opt/ai-paper-universe/data/deployment-verification.json` on the VPS and
`data/universe-deploy/deployment-verification.json` in the local workspace.

## Deployment

Use an isolated copy at `/opt/ai-paper-universe`, with the same locked Node
dependencies and its own compiled `dist/`. Do not replace `/opt/ai-paper-trader`.
The `deploy/ubuntu/ai-paper-universe-*` templates use loopback-only ports 3002
and 3003, databases in `/var/lib/ai-paper-universe-{two,five}`, and runner state
in `/home/trade-agent/paper-universe`.

Install the two environment files, backend template, market service/timer and
consumer service/timer. Create the runner root and its `market`, `two`, and
`five` subdirectories owned by `trade-agent`. Start only the new backends, then:

```sh
runuser -u trade-agent -- python3 -B /opt/ai-paper-universe/runner/paired.py \
  --root /home/trade-agent/paper-universe --initialize-universes
systemctl enable --now ai-paper-universe-market.timer \
  ai-paper-universe-agent@two.timer ai-paper-universe-agent@five.timer
```

Initialization requires fresh, isolated accounts. It pins the code version,
universe order, opening contexts and a common start slot at the next hour.
Never edit `startSlot`, reset the databases or overwrite pending runner state to
force trading. The first scheduled snapshot is at minute 02 after that start.

```sh
python3 -B /opt/ai-paper-universe/runner/paired_report.py \
  --root /home/trade-agent/paper-universe
python3 -B /opt/ai-paper-universe/runner/reconcile.py \
  /var/lib/ai-paper-universe-two/paper.sqlite
python3 -B /opt/ai-paper-universe/runner/reconcile.py \
  /var/lib/ai-paper-universe-five/paper.sqlite
```

Backend migration 0002 expands database symbol checks without altering balances,
trade IDs, context snapshots or idempotency receipts. The connection disables
foreign keys only around the migration transaction; the migration asserts all
references before commit and normal connections enforce them again.

Verification includes API BUY/SELL/replay and fee accounting for each added
coin, five-asset exposure and stale-quote checks, populated legacy migration,
universe selection and independent consumers. Run `pnpm typecheck`, `pnpm lint`,
`pnpm test`, `pnpm smoke`, Linux Python runner tests and `runner/universe_smoke.py`.
