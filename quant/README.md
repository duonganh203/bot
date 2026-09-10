# Historical strategy research

`backtest.py` compares fixed technical-rule candidates on BTCUSDT and ETHUSDT
hourly spot data. It uses Python 3's standard library and is separate from the
deployed runner. It does not invoke Codex, read credentials or the portfolio
database, submit signals, or change the trading strategy.

## Reproduce the comparison

Run from the repository root with Python 3.12 or later:

```bash
python3 -B -m unittest discover -s quant -p 'test_backtest.py' -v
python3 -B quant/backtest.py --start 2024-09-01 --split 2026-03-01 --end 2026-09-01
```

On Windows with the existing Ubuntu WSL installation:

```powershell
wsl -d Ubuntu --cd /mnt/d/Code/bot python3 -B -m unittest discover -s quant -p test_backtest.py -v
wsl -d Ubuntu --cd /mnt/d/Code/bot python3 -B quant/backtest.py
```

The first run downloads public Binance spot candles via GET, with no API key.
Missing or malformed data aborts the run. End dates are exclusive UTC midnights;
future/unclosed candles are rejected. Cached files include the requested range
plus 100 earlier warm-up bars. The report records their SHA-256 hashes and the
backtest source hash.

To reproduce results without any network requests after caching:

```bash
python3 -B quant/backtest.py --offline
```

Defaults use 18 months as a reference period and the following six months as a
chronological holdout. Parameters were fixed before the first run. There is no
training, optimization, grid search, or automatic strategy selection. Both
periods start with fresh $50 accounts while indicators use earlier history.

## Outputs

- `data/quant/candles/`: raw klines and request metadata, shared across reruns.
- `data/quant/comparison/report.md`: methodology, result tables and limitations.
- `data/quant/comparison/results.json`: every fill, hourly equity curve, summary
  metric, data hash, strategy definition and cost scenario. This is a large
  audit artifact (about 50 MB for the default comparison).

Use `--cache-dir` and `--output-dir` to keep separate experiments. Outputs are
gitignored; the reviewed initial findings are in
[docs/QUANT_BACKTEST.md](../docs/QUANT_BACKTEST.md).

## Scope and execution assumptions

- Active candidates: current trend-rule proxy, the same proxy without its
  positive-24h BUY gate, 20/10 breakout, and 20-bar z-score mean reversion.
- Benchmarks: $5 per coin buy-and-hold and $50 cash. The former is not a fully
  invested $50 portfolio or an exposure-matched risk-adjusted benchmark.
- Common sizing: one $5 entry per coin, no adding to an existing position;
  $20 marked BUY exposure cap; at most one action each hour, exits first.
- Entry/exit signals use fully closed candles; fills occur at next-hour opens,
  with 0.1% fees per side and adverse spread/slippage of 0/5/10 bps per side.
  This approximates, but does not reproduce, the deployed minute-02 execution.
- SELLs obey the $5 cap and six-decimal notional precision. An exit remains
  queued across hourly slots until its tradable quantity is sold. Sub-microdollar
  dust is retained and valued separately so it cannot freeze re-entry.
- The $3 UTC daily realized-loss limit blocks both BUY and SELL, like the
  backend. There are no intrabar stops; the mean-reversion time exit may be
  delayed by another pending exit, the order cap, or the daily-loss gate.
- Decimal cash/position accounting reconciles final equity against realized
  plus unrealized PnL including fees and dust. Indicators use floating point.
- Ending equity marks open positions without inventing final sale fills;
  `estimatedNetExitEquity` separately estimates disposal costs.

`trend_proxy` cannot reproduce historical AI judgments, confidence filtering,
live quotes, discretionary sizing or adding to positions. Its results must not
be described as the deployed AI bot's historical performance.

All return metrics include idle cash and assume USDT equals USD. Drawdown uses
hourly closes and misses intrahour extremes. Interest, depegs, exchange outages,
exchange lot filters and real order-book fills are not modeled. No profitability
claim follows from a single favorable period. Once a holdout informs changes,
it becomes development data; further assessment needs fresh data or forward
paper trading.
