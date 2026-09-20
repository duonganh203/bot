# V3: prospective paper comparison

This experiment evaluates one new candidate against the existing five-coin policy on fresh, separate $50 paper accounts. It does not establish a profitable strategy or enable exchange execution. Historical data inspected during development is already seen; it is development evidence, not an untouched out-of-sample test.

## Predefined comparison

| Item | Baseline | Candidate |
| --- | --- | --- |
| Universe | BTC, ETH, SOL, BNB, XRP, quoted in USDT | Same |
| Entry trend | Quote > SMA20 > SMA50; positive closed 24-hour return | Closed price > SMA72 > SMA168; positive closed 24-hour and 7-day returns |
| Entry opportunities | Every hour | Every six hours, at UTC-aligned slots |
| Priority | Existing fixed symbol order | Rank eligible assets by 7-day return divided by daily realized volatility |
| Entry size | $5 per coin | $5 × min(1, 2% / daily realized volatility), further limited by cash/exposure; skip below $1 |
| Re-entry | Existing policy | 24-hour cooldown per coin after a completed exit |
| Exits | Existing hourly sticky exit and loss gates | Closed price < SMA72 or 7-day return ≤ 0, or loss gates; complete partial liquidation hourly |
| Total entry exposure cap | $20 | $20 |
| Initial equity / trading fees | $50 / 0.1% each executed side | Same |
| AI calls | None | None |

The manifest records the exact rules, start slot, symbols, backends and version. Source code is authoritative for exact inequalities, volatility definition, rounding and cooldown state transitions. This is a bundled strategy change: a return difference cannot separately prove the contribution of trend horizon, ranking, sizing or cooldown.

All indicators use closed hourly candles. The common immutable market event supplies both consumers. Decisions, context, gates, requests, receipts and version identifiers are saved for audit. Failed, pending, rejected and missing requests must be visible separately from intentional HOLDs.

The $1 minimum is a paper-account assumption. It does not demonstrate executable minimum order size, depth or exchange suitability. The existing $3 initial-equity loss limit is an absolute floor relative to initial capital, not a trailing drawdown stop. The exposure cap gates new entries; market appreciation can raise marked exposure above $20. Orders and partial exits remain limited to $5 and at most one action per branch per hour.

## Freeze and prospective evaluation

1. Create fresh isolated accounts and a pinned manifest. Preserve V1/V2 histories separately.
2. Schedule a future start slot. Initialization and smoke-test/preflight activity are not forward performance.
3. Freeze the candidate before gathering its forward results. Changing signal, sizing, risk, asset selection or execution assumptions requires a new version and new prospective cohort.
4. Review coverage and reconciliation before interpreting returns. A rejected signal is an observed backend result; an expired unresolved request is pending, not a HOLD.
5. Review net equity, sampled drawdown, turnover, fees, exposure, passive benchmarks and cost scenarios together. Do not select on a short positive PnL snapshot.
6. Keep `promotion.eligible = false`. Neither a scheduled review nor a positive report automatically promotes the candidate or enables real-money trading.

The current report labels the evidence `insufficient_forward_evidence`. Calendar time alone does not change that designation. A later manual research review must assess sample size, number of completed trades, market-regime concentration, coverage and sensitivity to realistic costs. No significance or annualized Sharpe claim is made from a small live paper sample.

## Reporting and benchmarks

Run on the VPS:

```sh
python3 /opt/ai-paper-research-v3/runner/research_report.py \
  --root /home/trade-agent/paper-research-v3 \
  --output /home/trade-agent/paper-research-v3/reports/latest.json
```

An optional cost scenario adds a declared monthly operating cost **per branch**, using a 30-day month:

```sh
python3 /opt/ai-paper-research-v3/runner/research_report.py \
  --root /home/trade-agent/paper-research-v3 \
  --monthly-operating-cost-usd 1
```

Zero is labeled `not_measured`; it does not assert that infrastructure or research is free. Nonzero values are assumptions, not measured invoices. Actual shared infrastructure costs need a separately declared allocation method.

Paper net equity already includes backend trading fees. The report shows backend total fees and the sum of audited execution receipts, including any reconciliation difference. It adds incremental 5 and 10 basis-point cost scenarios on executed gross turnover. These are first-order cost deductions, **not backtests/replays** with altered fills, cash, gates or future decisions. Bid/ask spread observations are descriptive; they do not prove execution at either quote, depth, realized slippage or adverse selection.

Three passive references accompany each report:

- Cash: $50 held throughout.
- BTC plus cash: buy $20 gross BTC at the first scheduled shared event, deduct $0.02 entry fee, retain $29.98 cash.
- Equal-weight five coins plus cash: buy $4 gross per coin at the same event, deduct $0.02 aggregate entry fees, retain $29.98 cash.

Benchmark positions are marked at the report's common prices and are not liquidated. There is no final exit fee, spread/slippage deduction or rebalancing. Start prices come from `startSlot`, never initialization/preflight. If that event is absent, invested benchmarks remain unavailable rather than moving their start forward. Passive exposure drifts with prices and differs from active exposure; excess return against these references is not proof of risk-adjusted alpha.

Drawdown includes available hourly pre-decision equity, post-fill fee changes and report-time equity. It cannot observe intrahour extremes or missing marks. Mean exposure samples pre-decision holdings at completed slots; it is not a continuously measured time-weighted allocation. Coverage gaps must remain alongside these metrics.

## AI scope

AI assisted research, implementation and review. V3 makes no model calls and does not claim an AI trading edge. A future text/event feature experiment would require timestamped original sources, first-seen times, frozen extraction logic and a comparison with the same numerical policy without AI features. That work is not deployed here; it must not be inferred from the presence of an AI coding assistant.

## Isolation and operations

V3 source lives at `/opt/ai-paper-research-v3`, data at `/home/trade-agent/paper-research-v3`, and the independent loopback backends use ports 3004 and 3005. Existing experiments and data remain separate. Rollback means stopping V3 schedules/services while retaining its manifest, ledgers and evidence; it does not overwrite an older experiment's version or history.

The systemd templates collect at minute 04, run each independent consumer at
04:15 with bounded retries through 05:45, and save the report at 06:30 each
hour (UTC). Market events and completed decisions are idempotent across these
timer invocations. A missing slot is not backfilled with hindsight.

For a first installation, put an exact tested Git release in the source path,
install its frozen dependencies and build. As root from that source directory,
run `bash deploy/ubuntu/install-research-v3.sh`. The installer refuses existing
V3 databases, environment files, units or a manifest. It starts fresh backends,
checks their health, initializes a future start, runs preflight collection and
consumers, then enables schedules. Existing V2/universe units are not modified.
Do not use this first-install script as an upgrade or reset procedure.

```sh
systemctl status ai-paper-research-backend@candidate.service --no-pager
systemctl list-timers 'ai-paper-research-*' --no-pager
journalctl -u ai-paper-research-agent@candidate.service -n 30 --no-pager
python3 -B /opt/ai-paper-research-v3/runner/reconcile.py \
  /var/lib/ai-paper-research-candidate/paper.sqlite
```

To stop only V3 while retaining its data:

```sh
systemctl stop ai-paper-research-market.timer ai-paper-research-report.timer \
  ai-paper-research-agent@baseline.timer ai-paper-research-agent@candidate.timer
# Let any active consumer finish and inspect pending outcomes before stopping backends.
systemctl stop ai-paper-research-backend@baseline.service ai-paper-research-backend@candidate.service
```

Validation on 20 September 2026 passed: 87 backend tests; 88 Linux runner tests;
38 historical-research tests; TypeScript, lint and build; HTTP/restart/SQLite
smoke; V3 restart/recovery/isolation smoke; and existing V2/universe smoke.
A real Binance preflight also validated the longer candle history and book
telemetry. These are implementation checks, not evidence of profitability.

Implementation tests must cover closed-candle validation, policy boundaries, no-AI execution, idempotent recovery, account isolation, report accounting, prospective benchmark start, preflight exclusion and missing-slot classification. Run integration smoke checks against temporary isolated databases before enabling scheduled collection.
