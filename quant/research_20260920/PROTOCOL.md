# Quant research protocol - frozen before candidate replay outputs

Date: 2026-09-20. This is retrospective development research, not a preregistered
external study or untouched out-of-sample test. Historical windows and latest
forward PnL were already inspected. No parameter optimization or winner promotion.

## Questions and fixed interventions

H0: the current deterministic policy has no demonstrated positive net economic edge.
H1: completing queued reducing exits within the same hourly observation reduces
exit backlog and downside. Each child fill remains <= $5; entries stay one/hour.
H2: a 6% drawdown guard from the observed equity peak reduces peak drawdown.
The guard latches permanently for that simulation, forces liquidation and prevents
all subsequent entries. It does NOT reset monthly or recover automatically.
Test baseline, batch_exit, peak_guard, batch_peak (2x2 ablation); no threshold search.

Universes: BTC/ETH; BTC/ETH/SOL/BNB/XRP with $20 cap; the same five coins with
$10 cap as a capacity-control sensitivity. Fixed priority BTC ETH SOL BNB XRP.
Coins selected today, not a historical point-in-time universe: selection bias remains.

## Data and timing

Binance public hourly spot OHLCV, 2024-09-01 to 2026-09-20 exclusive UTC,
with 100 preceding warm-up hours. Missing, duplicated or invalid candles abort.
Shared runner/policy.py receives current hourly OPEN as quote and indicators
computed from completed candles through t-1. No current close/high/low used in
decisions. Same rule implementation, approximate quote timing; not exact minute-02
historical execution and not a replay of historical AI decisions.

## Portfolio and execution

Fresh $50/account; $5 entries, no adding above dust threshold $0.000001,
no borrowing/shorting, $3 UTC daily realized-loss gate and $47 absolute equity
floor, reducing sells permitted. Fee 10 bps/side. Adverse execution 0/5/10 bps
per side reruns the full path. Fill notional floored to 6 decimals for sells;
quantity rounded down to 24 decimals. Decimal ledger retains dust and checks
equity = initial + realized + unrealized. SELL amount is clipped at actual fill
price to prevent overselling after adverse slippage. No intrabar stops; no
order-book spread calibration, lot filters, tax, depeg or infrastructure costs.

Base case 5 bps. End positions remain marked; estimated disposal costs separately.
Peak uses previous hourly closes and current opens. The guard trigger is checked
at decision opens only (a close breach followed by an open recovery does not latch),
including entry fee check at the guard floor. Mark equity at each completed hourly close. Record
open/pre-fill and post-fill equity too for sampled drawdown/risk diagnostics.

## Evaluation windows / benchmarks

Continuous 2024-09-01 to 2026-09-01; eight non-overlapping three-month startup
windows anchored September/December/March/June; recent 2026-09-01 to 2026-09-20.
All start flat with $50. Do not compound or pool restarted accounts as one history.
Cash (0 interest) and passive total $10 equal-notional allocation to the branch
universe, one entry/coin in its first successive hours, no exits or strategy risk
overlay. Passive is fixed initial capital allocation, not realized exposure matched.

## Metrics / interpretation

Net portfolio return, marked and estimated-liquidation equity, observed peak DD,
fees/turnover/exposure, closed rounds/expectancy/win rate, entry blocks, first halt,
exit backlog and wait time. Compare batch and peak interventions against baseline
within the same window/universe/cost. Report every variant, including failures.

Historical uncertainty diagnostics on saved six-month daily returns: paired
circular block bootstrap (7-day primary; 1/14-day sensitivity), 5000 draws, fixed
seed. These resample realized paths, do not replay path-dependent loss gates and
assume approximate stationarity; interpret as conditional descriptions only.
Do not label percentile CIs predictive, selection-adjusted or statistical proof.
No DSR/PBO number without a complete valid trial inventory and required assumptions.

## Forward validation gate (proposal, not activated)

Freeze one candidate before starting an independent paper account. Primary outcome:
paired mean daily net return versus unchanged deterministic control; secondary
drawdown, exit backlog, exposure and coverage. Use actual cost logs, no reset/tuning
during evaluation. At least 90 calendar days and 30 closed rounds per branch before
an initial economic review; these are minimum process gates, not power guarantees.
Require positive excess return with dependence-aware uncertainty not spanning zero,
survival under 10 bps costs, and no unexplained reconciliation/coverage failures.
For multiple candidates use a declared family-wise correction, or one candidate
only. Revisions restart a fresh evaluation segment; no automatic live-money launch.
