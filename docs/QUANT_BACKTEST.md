# Initial BTC/ETH quant comparison — 2026-09-10

**Removing the 24-hour entry filter increased trading and worsened returns in
this experiment. No tested active strategy has demonstrated a robust advantage
across both periods and execution-cost scenarios. The deployed bot is unchanged.**

This is a fixed-parameter research comparison, not a replay of historical AI
decisions. See [the research runner](../quant/README.md) for commands and detailed
assumptions. Full fill logs and equity curves are generated locally under
`data/quant/comparison/`.

## Data and comparison design

- BTCUSDT and ETHUSDT hourly spot klines from Binance: September 1, 2024 through
  August 31, 2026, plus 100 warm-up candles; 17,620 candles per symbol.
- Reference period: September 1, 2024 to March 1, 2026, exclusive.
- Holdout: March 1, 2026 to September 1, 2026, exclusive (4,416 hourly slots).
- Each strategy/period starts with $50 cash. One $5 entry per coin, no adding
  to a position, one action per hour, exits first. The $20 marked BUY cap and
  $3 UTC daily realized-loss gate are retained, including the SELL block.
- Signals use completed candles and execute at the next hourly open. Base
  costs: 0.1% fee plus 0.05% adverse spread/slippage on each side.
- All figures below are returns on the entire $50 account, including idle cash.
  Open positions remain marked at the final close. Cash earns no interest.
- Parameters were fixed before inspecting results. No optimization or fitting
  was performed. The holdout is now observed and must not be reused to tune
  parameters while still being called an untouched test set.

Source: [Binance public spot candle API](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints).

## Base-case results

| Strategy | Reference return | Holdout return | Holdout max drawdown | Holdout BUY fills | Holdout fees | Holdout avg exposure |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Trend-rule proxy | -11.54% | -5.21% | 9.91% | 183 | $1.8242 | $4.08 |
| Trend without 24h BUY filter | -14.92% | -7.59% | 11.58% | 222 | $2.2134 | $4.25 |
| Breakout 20 / exit 10 | -9.39% | +1.01% | 4.02% | 110 | $1.0966 | $3.72 |
| Mean reversion | -10.44% | -5.80% | 6.81% | 155 | $1.5486 | $2.02 |
| Buy-and-hold: $5 per coin | -0.83% | +4.27% | 7.73% | 2 | $0.0100 | $10.36 |
| Cash | 0.00% | 0.00% | 0.00% | 0 | $0.0000 | $0.00 |

The buy-and-hold comparison has the same initial entry sizing, but substantially
more average market exposure. It is not a claim of superior risk-adjusted
performance or a recommendation to invest the full account.

## What the results support

1. **Loosening the 24h gate did not help here.** BUY fills rose from 183 to 222
   in the holdout, while account return declined from -5.21% to -7.59%. The
   reference period also deteriorated. This isolates one filter within the
   common sizing/exit policy; it does not test every possible trend strategy.
2. **Breakout is only a candidate for further observation.** It gained about
   $0.50 in the six-month holdout, but lost $4.69 over the reference period.
   Its holdout return was +2.11% with zero slippage (fees still included),
   +1.01% at 5 bps per side, and -0.09% at 10 bps per side.
3. **A higher win rate did not rescue this mean-reversion rule.** Its 54.19%
   closed-round-trip win rate coincided with a -5.80% account return. This
   result concerns this specific z-score rule, not the whole strategy family.
4. **HOLD remains normal under deterministic rules.** Breakout had a maximum
   gap of 203 hours without a BUY in the holdout. Frequent orders should not
   be the objective used to select a strategy.

There is insufficient evidence to replace the deployed strategy with any of
these candidates solely from this run. A forward paper comparison of a frozen
candidate would provide fresh evidence without optimizing against this holdout.

## Fixed rules

- **Trend proxy:** BUY when close > SMA20 > SMA50 and 24-hour return > 0.
  Exit when close < SMA20 and 24-hour return < 0. The relaxed variant removes
  only the positive-24h entry condition.
- **Breakout:** BUY when the close exceeds the highest high of the preceding
  20 candles, excluding the signal candle; exit below the preceding 10-candle low.
- **Mean reversion:** compute each candle's z-score relative to its preceding
  20 closes. BUY when it crosses from <= -2 to > -2; exit at that prior mean
  or after 24 elapsed hours from entry. No intrabar stop or assumption of
  instantaneous execution is made.

The trend proxy omits subjective AI choices and uses candle closes in place of
live quotes. Shared sizing prohibits adding to a position, which the deployed
runner permits. These numbers therefore are **not the AI bot's historical PnL**.

## Verification and reproducibility

- 15 standard-library tests passed on Ubuntu WSL: no-lookahead behavior, signal
  timing, data validation, both-side fees, partial exits, exit priority,
  cash/quantity/exposure limits, daily-loss blocking and UTC reset.
- All 36 strategy/period/cost combinations completed. Final Decimal ledgers
  reconciled equity with realized plus unrealized PnL. The cached offline rerun
  reproduced the results without network requests.
- No deployed API, database, timer, runner state, or trading policy was modified.

Raw-cache SHA-256 hashes:

- BTCUSDT: `cd1452331f43fc8bcce35e645b46c8443ed200875fa977cbe41f0f4b3775cac6`
- ETHUSDT: `488b02ce762c7072ec05af229b5e53549b6a314a7ec90f48a2fa7fb3315a8a00`

The generated JSON also records the backtest source hash and every fill.
