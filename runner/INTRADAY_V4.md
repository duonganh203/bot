# V4 prospective intraday paper experiment

V4 compares two entry hypotheses under identical execution, position sizing and
exit rules. The original V2, universe and V3 deployments remain separate, with
their manifests, data and source pins intact. This is a forward paper experiment;
neither entry hypothesis is established profitable and no live exchange orders
are submitted.

## Predeclared comparison

| Component | slow | pullback |
| --- | --- | --- |
| Entry trend | Closed 1h close > SMA72 > SMA168; positive 24h and 7d returns | Closed 4h close > SMA20 > SMA50 |
| Entry setup | Every six UTC hours | Previous closed 15m low touches EMA20; latest closed 15m close reclaims EMA20 and exceeds previous close |
| Entry opportunities | Once in the eligible 15m feature bar | Once per 15m feature bar |
| Ranking | 7d return / daily realized volatility | Same; positive 24h return is not required |
| Shared exits | Every minute: fixed entry fill minus 2 entry ATR14(15m), closed 4h close below SMA20, 12h maximum hold, equity/daily loss and sticky partial liquidation | Same |

Each account begins with $50. Orders are capped at $5, entry exposure at $20,
and sizing is $5 times min(1, 2% / daily volatility), with a $1 paper minimum.
Both branches have a 24h cooldown after a confirmed completed exit, no adding,
no shorts and no leverage. The backend independently enforces its existing
reduce-only-v2 limits: $3 UTC daily realized loss and a $47 equity entry floor.
These are entry restrictions and exit triggers, not guaranteed loss ceilings.
Parameters are fixed before forward observation, with no search on recent V3
results. The slow branch preserves V3's entry hypothesis; common V4 exits and
execution differ from V3, so it is not a literal replay of V3.

## Market data and modeled execution

Public Binance data uses 129 closed 15m, 337 closed 1h and 60 closed 4h candles.
The raw histories, derived features and checksum are stored once per 15m bar.
Continuity, UTC alignment, feature recomputation and exclusion of open candles
are validated. EMA20 starts at the first 20-close SMA; ATR14 uses Wilder smoothing.

Each immutable minute event contains decision and execution best bid/ask quotes
with displayed quantities. Execution collection starts at least two seconds
after decision collection completes. Both branches consume the same event.
Policy selection sees the decision book only; the later book can cancel an
entry on risk/liquidity grounds, but cannot select a different entry signal.

BUY fills at the delayed ask plus a fixed **2bps assumed adverse slippage**;
SELL fills at delayed bid minus 2bps, with adverse six-decimal rounding. The
backend charges **0.1% per side**. Displayed quantity is consumed across repeated
SELL chunks within a cycle. Maximum execution quote age is 45s, including a
15s transport margin before submission. Quote receive times and quote-to-paper
fill latency are local observations, not exchange execution timestamps.

This model does not simulate queue priority, cancellations or full-book impact.
Reported spread/slippage is already embedded in equity and is not subtracted
again. Extra 5/10bps turnover scenarios are sensitivities, not strategy replays.
The $1 minimum is a paper assumption, not live exchange filter compatibility.

## Recovery and evidence

Each exact request body and idempotency key is saved before submission.
V4 requests `X-Decimal-Format: string` for contexts and fills, preserving the
backend's decimal quantities through JSON; existing clients retain numeric
presentation by default. A
separate branch journal persists the pending request and confirmed position
metadata. Recovery applies each confirmed fill once, including after a lost
acknowledgement or backend restart. An expired unknown outcome blocks that
branch for reconciliation; do not delete its journal or invent a new key.

Risk exits run sequentially, refresh backend context/version each time, obey
the $5 limit, and may submit up to ten reducing chunks per minute. No BUY follows
an exit in the same cycle. Entry and stop metadata change only after confirmed
fills. Failed entry gates are recorded independently of scheduling gates.
Missing market minutes, interrupted cycles and pending outcomes stay visible in
reports. AI assists offline research and code review, with no per-candle LLM call.

## Deployment and verification

- Source: `/opt/ai-paper-intraday-v4`; state: `/home/trade-agent/paper-intraday-v4`.
- Backend ports: `127.0.0.1:3006` (slow), `127.0.0.1:3007` (pullback).
- Databases: `/var/lib/ai-paper-intraday-{slow,pullback}/paper.sqlite`.
- Market timer runs every minute; consumers retry at seconds 15, 30 and 45.
- Report: `reports/latest.json`; fresh bid valuation, fees, turnover, sampled
  drawdown, exposure, gate counts and paired evidence coverage.
- Initial review after 14 days is a review date, not a promotion threshold.

Use the first-install-only `deploy/ubuntu/install-intraday-v4.sh` from a built
release at its exact source path. It refuses existing V4 state/units, validates
two fresh isolated accounts and starts prospectively at the next 15m boundary.
Keep other experiment services and their source directories unchanged.

Linux checks: `python3 -m unittest discover -s runner`,
`python3 runner/intraday_smoke.py`, plus backend typecheck/lint/tests/build.
The HTTP smoke uses two disposable SQLite databases and synthetic features to
exercise an actual BUY, lost acknowledgement/restart recovery, two capped SELLs,
cooldown, report accounting and full ledger reconciliation. It does not estimate
profitability. Candle/no-lookahead and corruption checks run separately.

Sources for the public market API and exchange filter distinction:
[Binance market data](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)
and [filters](https://developers.binance.com/en/docs/products/spot/filters).
