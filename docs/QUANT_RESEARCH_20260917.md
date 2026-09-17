# Faster-entry research — September 17, 2026

**A faster entry rule did not establish a profitable replacement.** It raised
BUY fills from 6 to 16 in September 9–16, while net account return worsened
from -0.96% to -1.13%. All five active candidates lost money in that recent
sample, including with zero slippage and the 0.1% per-side fee retained.
The deployed V2 paper policy was not changed.

## Why the live bot held

At 06:03 UTC / 13:03 UTC+7 on September 17, the two live V2 paper accounts had
completed 44 common hourly events, all HOLD, with $50 cash each and no trades.
All backends and timers were active; both ledgers reconciled.

Read-only inspection of those 44 immutable market events found:

| Condition passing | BTC hours | ETH hours |
| --- | ---: | ---: |
| Quote above SMA20 | 12 | 11 |
| SMA20 above SMA50 | 4 | 3 |
| Closed-candle 24h return positive | 10 | 9 |
| All three together | 0 | 0 |

There was no eligible entry for the AI to approve or veto. The main constraint
was the joint trend filter, not a scheduler failure or AI reluctance.

## Fixed comparison

The existing research engine now optionally models `reduce-only-v2`: reducing
sells stay allowed after a daily loss, marked equity at/below $47 triggers
liquidation, BUY fees count against that floor, and BTC takes priority.
The default legacy mode and its six original candidates remain available.

The additional `trend_fast` candidate buys when the previous closed hourly
candle is above SMA5, SMA5 is above SMA20, and its six-hour return is positive.
It exits below SMA5 with a negative six-hour return. These settings were fixed
before this new run; there was no parameter search or subsequent tuning.

Seven strategies (five active, buy-and-hold, cash) were run across four
non-overlapping date windows and three execution-cost scenarios: **84 runs**.
Each window starts a fresh $50 paper account. Every entry is $5, no adding,
one action per hour, exits first; SELLs are capped at $5 with sticky partial
liquidation. Fee: 0.1% per side. Assumed adverse spread/slippage: 0/5/10 bps
per side. Tables use 5 bps (0.05%) per side. Idle cash is included in returns.

The original windows were already inspected earlier; they are not untouched
validation data for the new candidate. The recent windows contain eight days
each, too few to demonstrate durable profitability. Program output retains
the `reference`/`holdout` labels only to distinguish the two chronological windows.

## Returns after modeled costs

| Strategy | Sep 2024–Feb 2026 | Mar–Aug 2026 | Sep 1–8, 2026 | Sep 9–16, 2026 |
| --- | ---: | ---: | ---: | ---: |
| Current trend-rule proxy | -6.13% | -6.01% | -0.69% | -0.96% |
| Remove positive-24h BUY filter | -6.11% | -6.03% | -0.75% | -0.96% |
| Faster trend 5/20, 6h momentum | -6.01% | -6.07% | -0.62% | -1.13% |
| Breakout 20 / exit 10 | -6.47% | +1.22% | -0.05% | -0.50% |
| Mean reversion | -6.19% | -6.14% | -0.44% | -0.41% |
| Buy-and-hold, $5 per coin | -0.83% | +4.27% | +0.01% | -0.63% |
| Cash | 0.00% | 0.00% | 0.00% | 0.00% |

Buy-and-hold has substantially different average exposure; it is not a claim
of superior risk-adjusted performance. Many active historical runs stopped
taking entries after the equity-loss threshold, hence the cluster near -6%.
That threshold is relative to starting capital, not peak equity, and hourly
sampling/capped sells permit losses beyond $3. It does not cap peak drawdown
at 6% or guarantee any improvement in returns.

In the September 9–16 window:

| Strategy | BUY fills | Completed round trips | Fees | PnL on $50 |
| --- | ---: | ---: | ---: | ---: |
| Current trend-rule proxy | 6 | 6 | $0.0596 | -$0.4775 |
| Faster trend | 16 | 15 | $0.1546 | -$0.5652 |
| Breakout | 6 | 6 | $0.0598 | -$0.2506 |
| Mean reversion | 8 | 8 | $0.0799 | -$0.2062 |

The faster candidate's recent return worsened from -0.98% at 0 bps to -1.13%
at 5 bps and -1.43% at 10 bps. Breakout's favorable March–August result
(+1.22% at 5 bps) did not persist across the other three windows. These results
do not justify promoting any tested candidate as a way to earn quickly.

## Interpretation and next decision

Keep the existing paired forward experiment identifiable rather than resetting
its history or substituting a favorable historical slice. The current policy
also lacks demonstrated profitability: unchanged production is not an endorsement
of its economic edge. Any further candidate needs a stated reason for an edge,
cost sensitivity, and fresh forward paper evidence before promotion.

The system only simulates trades; no real exchange execution exists. For scale,
a $5 position gaining 1% earns about $0.03995 after 0.1% entry and exit fees,
before spread/slippage. Raising turnover alone does not change that arithmetic.

## Verification and reproduction

- 20 Python behavioral tests pass, covering fees, next-open fills, no future-data
  dependence for all seven candidates under both risk policies, daily/equity
  exits, gap overshoot, partial liquidation, and V2 priority.
- The 36 original legacy runs are compared against the saved original trades,
  equity curves, and summary fields to check backward compatibility.
- Commands and assumptions: [research README](../quant/README.md).
- Full generated artifacts (gitignored):
  `data/quant/v2-research-20260917-history/{report.md,results.json}` and
  `data/quant/v2-research-20260917-recent/{report.md,results.json}`.
- Each JSON records the research code hash, cached-data hashes, every fill,
  equity curve, rules, costs, and selected risk policy.

Source: [Binance public hourly klines](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market).
The recent cache covers September 1–16 plus 100 warm-up hours per symbol.
Signals use completed candles; fills use the following hourly open. This
approximates minute-02 live quotes and does not replay AI decisions. Terminal
positions remain marked rather than forcibly sold; `estimatedNetExitEquity`
separately estimates disposal costs. Costs are assumptions, not measured exchange
fills. Intrahour extrema, infrastructure costs and real exchange lot filters
are not modeled.
