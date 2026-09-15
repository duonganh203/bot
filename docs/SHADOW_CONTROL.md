# Forward paper control alongside the AI

Historical V1 design. The successor is [Quant V2](QUANT_V2.md); its independent
market producer removes the AI dependency and its common policy removes sizing
and exit differences. Keep V1 databases and evidence separate from V2 results.

The control buys when the fixed trend conditions qualify, without an AI veto.
It owns a fresh $50 account on a separate backend/database. The original AI
runner, prompt, portfolio and minute-02 timer continue unchanged.

This is an experiment to compare behavior and outcomes, not a claim that the
trend rule is profitable. Earlier historical tests of a similar rule lost money.

## Frozen rules

- Use the **same saved market snapshot** as a completed AI run in the current
  UTC hour. Recompute SMA20, SMA50 and the 24h return from its 100 closed candles.
- BUY $5 if `quote > SMA20 > SMA50` and closed-candle 24h return is positive.
  No adding to an existing position. When both qualify, BTC has fixed priority.
- SELL an existing position when `quote < SMA20` and the 24h return is negative.
  Exits take priority over entries; once triggered, finish partial exits in
  subsequent hours even if the trend recovers. BTC wins ties between exits.
- At most one action each hour, $5 per order, $20 marked BUY exposure, cash
  including fees, no shorting. The same $3 UTC daily realized-loss gate blocks
  both BUY and SELL. This known exit limitation is retained for comparability.
- Keep API dust under $0.000001 in the ledger; it does not prevent a new entry.
- 0.1% fee per side, same quote as AI, no spread/slippage. A control fill occurs
  later at that observed quote, so it is not evidence of executable live prices.
- Required API `confidence=1` means the deterministic rule matched, not a
  calibrated win probability. No model is called by the control.

`runner/shadow.py` and its shared `runner/run.py` bytes determine the control
strategy hash. Initialization also pins the AI strategy label. Either changing
blocks new analyses, requiring an explicit new experiment. Pending deliveries
are recovered using their exact saved payload/key before new analysis.

The experiment starts at the **next UTC hour after initialization**. It never
imports old decisions into its portfolio, never backfills expired prices, and
never forces a trade just because enough time passed. It needs a completed AI
snapshot; AI failures/missing slots are missing control coverage too.

## Isolation and audit

- AI: port 3000, existing database and runner state.
- Control: loopback-only port 3001, `/var/lib/ai-paper-trader-shadow/paper.sqlite`.
- Control state: `/home/trade-agent/paper-shadow/state/`.
- The runner checks cross-context lookups in both backends return 404. Different
  ports alone are insufficient proof of separate databases. Failed isolation
  checks abort before any order. Cash, fee and hard-risk settings are checked.
- The control backend runs as `paper-trader-shadow`; its service can write only
  the separate database directory. The runner's service can write its own state
  and has read-only access to the original runner evidence.
- `experiment.json` pins start slot, versions, policy and review horizon.
- `runs/*/evidence.json` stores source run/context IDs, complete market snapshot,
  control pre-decision context, per-symbol gates, chosen action and AI comparison.
- `request.json` and `state.json` are durable before POST. Backend receipts make
  retries safe. HTTP 422 is a completed rejection, not an invented fill.
- The existing AI `snapshot.json` is selected only through the completed primary
  `state.json`; unrelated dry runs are not selected.

Comparison labels describe **observed actions**, not AI approval of a proposed
control trade. The AI reasons about its own account. Records explicitly flag
whether pre-decision cash and inventory match. After the portfolios diverge,
the experiment cannot isolate the causal contribution of the AI entry filter.
It also records AI order rejection separately from matching intended actions.

## Install on the existing Ubuntu VPS

Publish/copy the new files into `/opt/ai-paper-trader` first. They do not require
changing the existing runner or rebuilding the backend. Verify its existing
`dist/`, dependencies and migrations are available.

```bash
cd /opt/ai-paper-trader
python3 -B -m unittest discover -s runner -p 'test_*.py' -v
python3 -B runner/shadow_smoke.py
id paper-trader-shadow || useradd --system --user-group \
  --home-dir /var/lib/ai-paper-trader-shadow --shell /usr/sbin/nologin paper-trader-shadow
install -d -o paper-trader-shadow -g paper-trader-shadow -m 0700 /var/lib/ai-paper-trader-shadow
install -d -o trade-agent -g trade-agent -m 0700 /home/trade-agent/paper-shadow
install -m 0644 deploy/ubuntu/ai-paper-trader-shadow.service /etc/systemd/system/
install -m 0644 deploy/ubuntu/ai-paper-trader-shadow-agent.service /etc/systemd/system/
install -m 0644 deploy/ubuntu/ai-paper-trader-shadow-agent.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/ai-paper-trader-shadow*.service /etc/systemd/system/ai-paper-trader-shadow-agent.timer
systemctl daemon-reload
systemctl enable --now ai-paper-trader-shadow.service
curl --fail-with-body http://127.0.0.1:3001/health
runuser -u trade-agent -- python3 -B /opt/ai-paper-trader/runner/shadow.py --initialize
systemctl enable --now ai-paper-trader-shadow-agent.timer
systemctl list-timers ai-paper-trader-shadow-agent.timer --no-pager
```

Never use the original database path or reset its portfolio. Initialization is
idempotent with the same frozen configuration and requires a fresh control
portfolio. Do not delete state or reinitialize to force extra entries.

The smoke command starts two temporary backend processes on free loopback ports
and writes only their disposable databases under `data/shadow-smoke-*`. It checks
a real control BUY against a synthetic AI HOLD, isolation, HTTP replay after
restart, accounting and the comparison report. It does not touch either running
portfolio. Requires the existing compiled backend and Node dependencies.

The timer tries at **minute 03:30, 04:30 and 05:30 UTC**, following the AI's
minute-02 run. Completed slots are skipped. Waiting for an absent AI outcome
returns `waiting`; later attempts may find it. Quotes expire under the same
240-second source-age ceiling and 15-second transport margin. A stale snapshot
fails without an order. No past hour is caught up after downtime.

## Inspect outcomes and stop

```bash
systemctl status ai-paper-trader-shadow-agent.timer --no-pager
journalctl -u ai-paper-trader-shadow-agent.service -n 60 --no-pager
curl --fail-with-body 'http://127.0.0.1:3001/api/review?days=14'
runuser -u trade-agent -- python3 -B /opt/ai-paper-trader/runner/shadow_report.py \
  --output /home/trade-agent/paper-shadow/latest-report.json
```

The report fetches current market prices and creates marked context snapshots in
both backends. It never submits signals. It reports equity/positions, control
fills, action agreement, missing slots, pending requests and drawdown at observed
marks. Terminal JSON plus the saved report are the initial interface; no public
port, dashboard or automatic notification is enabled.

Stop future control runs with `systemctl disable --now ai-paper-trader-shadow-agent.timer`.
Let an active run finish; do not remove pending requests. The original AI timer
and both ledgers stay intact. Stopping the experiment does not liquidate holdings.

## Evaluation fixed before collecting outcomes

Review after **14 calendar days from initialization**: account equity including
open positions, observed drawdown, total fees, completed round trips, order/exit
behavior, missing slots and action agreement. Compare only overlapping coverage
and explain differences in starting inventory, sizing and exposure. Inspect
spread/slippage sensitivity before interpreting any apparent advantage.

No automatic activation, risk-limit change or promotion follows from the report.
If there are too few independent outcomes, keep that limitation explicit rather
than treating a chosen trade count or two-week date as proof of profitability.
Do not tune the policy mid-experiment; version any new candidate separately.
