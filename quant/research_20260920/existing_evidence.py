"""Recompute saved research evidence; never contacts an exchange or changes bot state.

Run from the repository root with Python + numpy. Optional matplotlib writes figures.
Saved windows are development evidence, including those originally named 'holdout'.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/quant/research-20260920"
sys.path.insert(0, str(OUT / "python-deps"))
import numpy as np

D = Decimal
HOUR = timedelta(hours=1)
SEED = 20260920
RESAMPLES = 5000
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def daily_returns(curve, initial=50.0):
    """A mark at 00:00 is the close of the preceding UTC day, not the new day."""
    closes = {}
    for point in curve:
        day = (dt(point["time"]) - timedelta(microseconds=1)).date().isoformat()
        closes[day] = point["equity"]
    previous = float(initial)
    result = []
    for day, equity in closes.items():
        result.append({"date": day, "equity": equity, "return": equity / previous - 1})
        previous = equity
    return result


def monthly_returns(daily, initial=50.0):
    closes = {}
    counts = defaultdict(int)
    for item in daily:
        month = item["date"][:7]
        closes[month] = item["equity"]
        counts[month] += 1
    previous = float(initial)
    result = []
    for month, equity in closes.items():
        result.append({"month": month, "observedDays": counts[month], "endEquity": equity,
                       "returnPct": 100 * (equity / previous - 1)})
        previous = equity
    return result


def return_diagnostics(values, lag=7):
    r = np.asarray(values, dtype=float)
    mean = float(np.mean(r))
    std = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    x = r - mean
    lrv = float(np.dot(x, x) / len(x))
    actual_lag = min(lag, len(x) - 1)
    for k in range(1, actual_lag + 1):
        gamma = float(np.dot(x[k:], x[:-k]) / len(x))
        lrv += 2 * (1 - k / (actual_lag + 1)) * gamma
    # A long-run-variance adjustment is diagnostic only. Suppress on tiny windows.
    return {"days": len(r), "meanDailyBps": mean * 10000, "stdDailyBps": std * 10000,
            "annualizedVolatilityPct": std * math.sqrt(365) * 100,
            "naiveDailySharpeAnnualized365": mean / std * math.sqrt(365) if std > 1e-15 else None,
            "hacLagDays": actual_lag,
            "hacLongRunVariance": lrv,
            "hacSharpeAnnualized365": mean / math.sqrt(lrv) * math.sqrt(365)
            if len(r) >= 30 and lrv > 1e-24 else None,
            "positiveDays": int(np.count_nonzero(r > 1e-10)),
            "negativeDays": int(np.count_nonzero(r < -1e-10)),
            "effectivelyFlatDays": int(np.count_nonzero(np.abs(r) <= 1e-10))}


def circular_block_mean_ci(values, block_length, resamples=RESAMPLES, seed=SEED):
    """Percentile CI of mean using circular blocks; paired differences enter together.

    The uncentered resampling fraction is not a p-value or posterior probability.
    This resamples returns, not an adaptive trading policy on new price paths.
    """
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or not len(x) or not 1 <= block_length <= len(x):
        raise ValueError("Expected a nonempty vector and 1 <= block length <= n")
    rng = np.random.default_rng(seed)
    n = len(x)
    starts = rng.integers(0, n, size=(resamples, math.ceil(n / block_length)))
    idx = (starts[..., None] + np.arange(block_length)) % n
    idx = idx.reshape(resamples, -1)[:, :n]
    means = np.mean(x[idx], axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return {"blockDays": block_length, "resamples": resamples, "seed": seed,
            "observedMeanDailyExcessBps": float(np.mean(x) * 10000),
            "percentile95MeanDailyExcessBps": [float(low * 10000), float(high * 10000)],
            "fractionResampledMeansAboveZero": float(np.mean(means > 0)),
            "bootstrapMeanStdErrorBps": float(np.std(means, ddof=1) * 10000)}


class Ledger:
    """Independent replay of published fill records, with pro-rata cost and dust."""

    def __init__(self, initial=50):
        self.cash = D(str(initial))
        self.positions = {}
        self.dust = defaultdict(Decimal)
        self.dust_cost = D(0)
        self.fees = D(0)
        self.realized = D(0)
        self.completed = []
        self.errors = defaultdict(float)

    def check(self, name, expected, actual):
        error = abs(float(expected) - float(actual))
        self.errors[name] = max(self.errors[name], error)
        if error > 1e-8:
            raise ArithmeticError(f"{name} mismatch: expected={expected}, actual={actual}")

    def exposure(self, marks):
        return sum(((self.positions.get(s, {}).get("quantity", D(0)) + self.dust[s]) * marks[s]
                    for s in SYMBOLS), D(0))

    def fill(self, trade):
        symbol, action = trade["symbol"], trade["action"]
        price, gross, fee = (D(trade[k]) for k in ("price", "amountUsd", "feeUsd"))
        self.check("feeRate", gross * D("0.001"), fee)
        quantity = gross / price
        self.fees += fee
        if action == "BUY":
            if symbol in self.positions:
                raise ArithmeticError("Unexpected pyramiding")
            self.check("entryOrderUsd", D(5), gross)
            self.cash -= gross + fee
            self.positions[symbol] = {"quantity": quantity, "cost": gross, "entryFee": fee,
                                      "entered": dt(trade["time"]), "pnl": D(0), "sellFills": 0}
            self.check("buyRealizedPnl", 0, trade["realizedPnl"])
        elif action == "SELL":
            p = self.positions[symbol]
            if gross > 5 or gross <= 0 or quantity > p["quantity"] + D("1e-24"):
                raise ArithmeticError("Invalid capped sell")
            fraction = quantity / p["quantity"]
            allocated_cost, allocated_fee = p["cost"] * fraction, p["entryFee"] * fraction
            realized = gross - fee - allocated_cost - allocated_fee
            self.check("fillRealizedPnl", realized, trade["realizedPnl"])
            p["quantity"] -= quantity
            p["cost"] -= allocated_cost
            p["entryFee"] -= allocated_fee
            p["pnl"] += realized
            p["sellFills"] += 1
            self.realized += realized
            self.cash += gross - fee
            if p["quantity"] * price < D("0.000001"):
                self.completed.append({"symbol": symbol, "entryTime": iso(p["entered"]),
                                       "exitTime": trade["time"], "netPnlUsd": float(p["pnl"]),
                                       "holdingHours": (dt(trade["time"]) - p["entered"]).total_seconds() / 3600,
                                       "sellFills": p["sellFills"]})
                self.dust[symbol] += p["quantity"]
                self.dust_cost += p["cost"] + p["entryFee"]
                del self.positions[symbol]
        else:
            raise ArithmeticError("Unknown fill side")
        if self.cash < 0:
            raise ArithmeticError("Negative cash")


def load_prices(source):
    candles, metadata = {}, []
    for item in source["data"]:
        # Relocate within this repository instead of relying on saved absolute paths.
        path = ROOT / "data/quant/candles" / Path(item["path"].replace("\\", "/")).name
        file_hash = digest(path)
        if file_hash != item["sha256"]:
            raise ArithmeticError(f"Candle source hash mismatch: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        prices = {}
        previous = None
        for row in raw["rows"]:
            time = datetime.fromtimestamp(row[0] / 1000, timezone.utc)
            if previous is not None and time - previous != HOUR:
                raise ArithmeticError("Non-contiguous candle history")
            previous = time
            if row[6] != row[0] + 3599999:
                raise ArithmeticError("Invalid candle close timestamp")
            op, hi, lo, cl, vol = map(float, row[1:6])
            if not (0 < lo <= min(op, cl) <= max(op, cl) <= hi and vol >= 0):
                raise ArithmeticError("Invalid candle OHLCV")
            prices[time] = {"open": D(str(op)), "close": D(str(cl))}
        candles[item["symbol"]] = prices
        metadata.append({"symbol": item["symbol"], "path": str(path.relative_to(ROOT)),
                         "sha256": file_hash, "candles": len(prices),
                         "source": raw["source"], "fetchedAt": raw.get("fetchedAt")})
    return candles, metadata


def analyze_run(run, prices, dataset):
    s, curve, trades = run["summary"], run["equityCurve"], run["trades"]
    ledger = Ledger(s["initialEquity"])
    by_time = {}
    for trade in trades:
        time = dt(trade["time"])
        if time in by_time:
            raise ArithmeticError("More than one action in an hour")
        if dt(trade["signalCandleOpen"]) != time - HOUR:
            raise ArithmeticError("Signal candle is not the preceding closed bar")
        by_time[time] = trade
    start, end = dt(s["start"]), dt(s["endExclusive"])
    if len(curve) != s["hours"] or len(curve) != int((end - start).total_seconds() / 3600):
        raise ArithmeticError("Curve length mismatch")
    daily_pnl, day = D(0), None
    blocked = daily_blocked = equity_blocked = active = 0
    blocked_streak = longest_blocked = no_buy = longest_no_buy = 0
    first_blocked = first_below_floor = None
    matched_trades = 0
    exposures = []
    equity_values = [s["initialEquity"]]
    for i, point in enumerate(curve):
        time = start + i * HOUR
        if dt(point["time"]) != time + HOUR:
            raise ArithmeticError("Non-contiguous equity timestamps")
        if time.date() != day:
            day, daily_pnl = time.date(), D(0)
        opens = {symbol: prices[symbol][time]["open"] for symbol in SYMBOLS}
        closes = {symbol: prices[symbol][time]["close"] for symbol in SYMBOLS}
        open_equity = ledger.cash + ledger.exposure(opens)
        daily_gate = daily_pnl <= -3
        equity_gate = open_equity - D("0.005") <= 47
        daily_blocked += daily_gate
        equity_blocked += equity_gate
        blocked += daily_gate or equity_gate
        blocked_streak = blocked_streak + 1 if daily_gate or equity_gate else 0
        longest_blocked = max(longest_blocked, blocked_streak)
        if first_blocked is None and (daily_gate or equity_gate):
            first_blocked = iso(time)
        trade = by_time.get(time)
        bought = bool(trade and trade["action"] == "BUY")
        if trade:
            matched_trades += 1
            side = 1 if bought else -1
            expected_price = opens[trade["symbol"]] * (1 + side * D(str(s["slippageBpsPerSide"])) / 10000)
            ledger.check("executionPrice", expected_price, trade["price"])
            before_realized = ledger.realized
            ledger.fill(trade)
            daily_pnl += ledger.realized - before_realized
        no_buy = 0 if bought else no_buy + 1
        longest_no_buy = max(longest_no_buy, no_buy)
        exposure = ledger.exposure(closes)
        equity = ledger.cash + exposure
        ledger.check("hourlyExposure", exposure, point["exposure"])
        ledger.check("hourlyEquity", equity, point["equity"])
        ledger.check("hourlyCashIdentity", ledger.cash, point["equity"] - point["exposure"])
        if first_below_floor is None and equity < 47:
            first_below_floor = point["time"]
        active += bool(ledger.positions)
        exposures.append(float(exposure))
        equity_values.append(float(equity))
    if matched_trades != len(trades):
        raise ArithmeticError("Unmatched fills outside the evaluation window")
    values = np.asarray(equity_values)
    peaks = np.maximum.accumulate(values)
    drawdown = (peaks - values) / peaks * 100
    remaining_cost = ledger.dust_cost + sum((p["cost"] + p["entryFee"] for p in ledger.positions.values()), D(0))
    unrealized = ledger.exposure(closes) - remaining_cost
    pnls = np.asarray([r["netPnlUsd"] for r in ledger.completed])
    holding = np.asarray([r["holdingHours"] for r in ledger.completed])
    gains, losses = float(np.sum(pnls[pnls > 0])), float(-np.sum(pnls[pnls < 0]))
    win_rate = 100 * float(np.mean(pnls > 0)) if len(pnls) else None
    pf = gains / losses if losses else None
    daily = daily_returns(curve, s["initialEquity"])
    diagnostics = return_diagnostics([p["return"] for p in daily])
    recomputed = {"endEquity": values[-1], "returnPct": 100 * (values[-1] / values[0] - 1),
                  "pnlUsd": values[-1] - values[0], "feesUsd": ledger.fees,
                  "realizedPnlUsd": ledger.realized, "unrealizedPnlUsd": unrealized,
                  "maxDrawdownPct": float(np.max(drawdown)), "averageExposureUsd": float(np.mean(exposures)),
                  "openExposureUsd": exposures[-1], "timeInMarketPct": active / len(curve) * 100,
                  "buyFills": sum(t["action"] == "BUY" for t in trades),
                  "sellFills": sum(t["action"] == "SELL" for t in trades),
                  "closedRoundTrips": len(pnls), "winRatePct": win_rate, "profitFactor": pf,
                  "riskBlockedHours": blocked, "dailyLossBlockedHours": daily_blocked,
                  "equityLossBlockedHours": equity_blocked, "longestNoBuyHours": longest_no_buy,
                  "untradeableDustValueUsd": sum((ledger.dust[z] * closes[z] for z in SYMBOLS), D(0)),
                  "dailySharpeZeroCashRate": diagnostics["naiveDailySharpeAnnualized365"],
                  "estimatedNetExitEquity": float(ledger.cash) + exposures[-1] *
                  (1 - s["slippageBpsPerSide"] / 10000) * (1 - 0.001)}
    for name, actual in recomputed.items():
        if actual is None or s[name] is None:
            if actual is not s[name]:
                raise ArithmeticError(f"Null mismatch for {name}")
        else:
            ledger.check("summary." + name, s[name], actual)
    ledger.check("finalPnlIdentity", values[-1], D(str(values[0])) + ledger.realized + unrealized)
    metrics = {**{k: float(v) if isinstance(v, (Decimal, np.floating)) else v for k, v in recomputed.items()},
               **diagnostics,
               "completedTradeExpectancyUsd": float(np.mean(pnls)) if len(pnls) else None,
               "completedTradeMedianPnlUsd": float(np.median(pnls)) if len(pnls) else None,
               "averageWinningTradeUsd": float(np.mean(pnls[pnls > 0])) if gains else None,
               "averageLosingTradeUsd": float(np.mean(pnls[pnls < 0])) if losses else None,
               "meanHoldingHours": float(np.mean(holding)) if len(holding) else None,
               "medianHoldingHours": float(np.median(holding)) if len(holding) else None,
               "maxHoldingHours": float(np.max(holding)) if len(holding) else None,
               "averageExposurePctInitialCapital": float(np.mean(exposures)) / values[0] * 100,
               "averageExposurePctEquity": float(np.mean(np.asarray(exposures) / values[1:])) * 100,
               "riskBlockedPctHours": blocked / len(curve) * 100,
               "longestRiskBlockedHours": longest_blocked, "firstRiskBlockedTime": first_blocked,
               "firstCloseBelow47": first_below_floor, "minEquityUsd": float(np.min(values)),
               "maxEquityUsd": float(np.max(values)), "totalFillNotionalUsd": sum(float(t["amountUsd"]) for t in trades),
               "feesPctInitialCapital": float(ledger.fees) / values[0] * 100,
               "openPositionCount": len(ledger.positions)}
    return {"dataset": dataset, "periodOriginalLabel": run["period"], "status": "development",
            "strategy": s["strategy"], "start": s["start"], "endExclusive": s["endExclusive"],
            "slippageBpsPerSide": s["slippageBpsPerSide"], "sourceSummary": s,
            "metrics": metrics, "daily": daily, "monthly": monthly_returns(daily, s["initialEquity"]),
            "completedTrades": ledger.completed,
            "validation": {"passed": True, "hourlyMarksChecked": len(curve), "fillsChecked": len(trades),
                           "maxAbsoluteErrors": dict(ledger.errors)}}


def compare_runs(runs):
    for run in runs:
        group = [x for x in runs if (x["dataset"], x["start"], x["endExclusive"], x["slippageBpsPerSide"]) ==
                 (run["dataset"], run["start"], run["endExclusive"], run["slippageBpsPerSide"])]
        own = np.asarray([x["return"] for x in run["daily"]])
        run["comparisons"] = {}
        for benchmark in ("cash", "buy_hold"):
            other = next(x for x in group if x["strategy"] == benchmark)
            if [x["date"] for x in run["daily"]] != [x["date"] for x in other["daily"]]:
                raise ArithmeticError("Benchmark dates differ")
            excess = own - np.asarray([x["return"] for x in other["daily"]])
            run["comparisons"][benchmark] = {
                "meanDailyExcessBps": float(np.mean(excess) * 10000),
                "accountReturnDifferencePercentagePoints": run["metrics"]["returnPct"] - other["metrics"]["returnPct"],
                "dailyExcessReturns": excess.tolist()}
            if run["dataset"] == "history" and run["periodOriginalLabel"] == "holdout" and run["slippageBpsPerSide"] == 5:
                run["comparisons"][benchmark]["circularBlockBootstrap"] = [
                    circular_block_mean_ci(excess, block) for block in (7, 1, 14)]


def figures(runs, out):
    os.environ.setdefault("MPLCONFIGDIR", str(out / "matplotlib-cache"))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return {"generated": False, "reason": "matplotlib unavailable; plot-ready data are in JSON"}
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False})
    selected = [r for r in runs if r["dataset"] == "history" and r["periodOriginalLabel"] == "holdout"
                and r["slippageBpsPerSide"] == 5]
    colors = {"trend_proxy": "#386cb0", "trend_no_24h": "#b275b4", "trend_fast": "#ef6c35",
              "breakout": "#169b83", "mean_reversion": "#c85064", "buy_hold": "#222222", "cash": "#9a9a9a"}
    fig, ax = plt.subplots(figsize=(10, 5))
    for run in selected:
        days = [dt(run["start"])] + [dt(x["date"] + "T00:00:00Z") + timedelta(days=1) for x in run["daily"]]
        values = [50] + [x["equity"] for x in run["daily"]]
        ax.plot(days, values, label=run["strategy"], color=colors[run["strategy"]], linewidth=1.5)
    ax.axhline(47, color="#c85064", linestyle=":", linewidth=1)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.set(ylabel="Account equity (USD)", title="Saved development window: March–August 2026")
    ax.grid(alpha=0.18)
    ax.legend(loc="upper left", ncol=2, fontsize=8)
    fig.text(0.01, 0.01, "$50 initial account | 0.1% fee + 5 bps slippage per side | BTC/ETH | no AI replay", fontsize=9)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    paths = []
    path = out / "existing-equity-mar-aug.png"
    fig.savefig(path, dpi=180)
    paths.append(str(path.relative_to(ROOT)))
    plt.close(fig)
    candidates = [r for r in selected if r["strategy"] not in ("cash", "buy_hold")]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    for ax, benchmark in zip(axes, ("cash", "buy_hold")):
        for j, r in enumerate(candidates):
            b = r["comparisons"][benchmark]["circularBlockBootstrap"][0]
            lo, hi = b["percentile95MeanDailyExcessBps"]
            mean = b["observedMeanDailyExcessBps"]
            ax.plot([lo, hi], [j, j], color=colors[r["strategy"]], linewidth=3)
            ax.scatter([mean], [j], color=colors[r["strategy"]], s=35, zorder=3)
        ax.axvline(0, color="#555555", linestyle="--", linewidth=1)
        ax.set_title("Daily excess versus " + benchmark)
        ax.set_xlabel("Mean daily account excess return (bps)")
        ax.set_yticks(range(len(candidates)), [r["strategy"] for r in candidates])
        ax.grid(axis="x", alpha=0.18)
    axes[0].invert_yaxis()
    fig.suptitle("Conditional path diagnostics: 95% circular block bootstrap intervals")
    fig.text(0.01, 0.01, "184 days | 7-day blocks | 5,000 resamples | no selection correction; not an out-of-sample edge test", fontsize=9)
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    path = out / "existing-bootstrap-excess.png"
    fig.savefig(path, dpi=180)
    paths.append(str(path.relative_to(ROOT)))
    plt.close(fig)
    return {"generated": True, "paths": paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    result = {"analysisDate": "2026-09-20", "scriptSha256": digest(Path(__file__)),
              "environment": {"python": sys.version, "numpy": np.__version__},
              "method": {"dailyConvention": "UTC close; point at 00:00 belongs to previous day; initial equity $50 included",
                         "bootstrap": "Paired daily return differences; circular blocks 7 days primary, 1/14 sensitivity; percentile 95%; 5000 resamples; seed 20260920",
                         "hac": "Bartlett-weighted autocovariances using denominator n, lag 7, sqrt(365) annualization; suppressed for <30 observations",
                         "completedTrades": "Reconstructed full round trips; partial fills pooled, net of allocated entry/exit fees; untradeable dust retained separately",
                         "validation": "All saved hourly equity and exposures rebuilt from fill ledger and source candle closes; fees, realized/unrealized PnL, next-open prices, daily timing, drawdown, risk-blocked hours and summary metrics checked",
                         "notValidated": "This script does not independently regenerate indicator signals or choice of trade actions; see policy replay audit"},
              "limitations": ["All four windows are development evidence; original holdout labels do not establish unseen data.",
                              "Bootstrap is conditional on the observed path and assumes local dependence suitable for block resampling; risk-floor shutdown violates stationarity and can make uncertainty appear small.",
                              "Bootstrap does not replay adaptive capital/risk state on resampled price paths. Intervals are diagnostics, not confidence in future profit or multiple-testing-adjusted evidence.",
                              "No trade-IID resampling, no p-values, no posterior win probabilities, no OOS Sharpe claims.",
                              "buy_hold deploys $5 per coin and retains cash; it is not a fully invested benchmark. The saved engine also applies the common risk overlay to it.",
                              "Each window restarts with $50; windows must not be concatenated as a continuous traded equity curve.",
                              "Returns exclude funding opportunity cost, AI/VPS operating costs, and live execution uncertainty beyond fixed slippage."],
              "sources": [], "runs": []}
    for dataset in ("history", "recent"):
        path = ROOT / f"data/quant/v2-research-20260917-{dataset}/results.json"
        source = json.loads(path.read_text(encoding="utf-8"))
        prices, metadata = load_prices(source)
        result["sources"].append({"dataset": dataset, "path": str(path.relative_to(ROOT)),
                                  "sha256": digest(path), "savedEngineCodeSha256": source["codeSha256"],
                                  "generatedAt": source["generatedAt"], "candleSources": metadata})
        for run in source["runs"]:
            result["runs"].append(analyze_run(run, prices, dataset))
        print(f"Validated {dataset}: {len(source['runs'])} runs", flush=True)
    compare_runs(result["runs"])
    result["validationTotals"] = {"runs": len(result["runs"]), "allPassed": True,
                                  "hourlyMarks": sum(r["validation"]["hourlyMarksChecked"] for r in result["runs"]),
                                  "fills": sum(r["validation"]["fillsChecked"] for r in result["runs"])}
    result["figures"] = figures(result["runs"], OUT) if not args.no_figures else {"generated": False, "reason": "disabled"}
    target = OUT / "existing-statistics.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result["validationTotals"]))
    print(target)


if __name__ == "__main__":
    main()
