#!/usr/bin/env python3
"""Offline spot strategy comparison. Public GETs only; never accesses portfolio APIs."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HOUR = 3_600_000
SYMBOLS = ("BTCUSDT", "ETHUSDT")
BASE = "https://data-api.binance.vision"
D = Decimal
CAPITAL, ORDER, EXPOSURE, DAILY_LOSS = D("50"), D("5"), D("20"), D("3")
FEE = D("0.001")
STRATEGIES = ("trend_proxy", "trend_no_24h", "breakout", "mean_reversion", "buy_hold", "cash")
EXPERIMENTAL_STRATEGIES = ("trend_fast",)
RISK_POLICIES = ("legacy-v1", "reduce-only-v2")
RULES = {
    "trend_proxy": "BUY close > SMA20 > SMA50 and 24h return > 0; exit close < SMA20 and 24h return < 0.",
    "trend_no_24h": "Same as trend_proxy, removing ONLY the 24h filter from BUY; identical exit.",
    "breakout": "BUY close > high of prior 20 candles (excluding signal candle); exit close < low of prior 10.",
    "mean_reversion": "BUY when z-score crosses back above -2 from <= -2, relative to each candle's prior 20 closes; exit at prior-20 mean or after 24 elapsed hours from entry.",
    "buy_hold": "One $5 entry per symbol at the first available execution slots; hold to period end.",
    "cash": "Keep all $50 in cash; no interest accrual.",
    "trend_fast": "BUY close > SMA5 > SMA20 and 6h return > 0; exit close < SMA5 and 6h return < 0. Fixed research candidate added 2026-09-17.",
}


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def parse_date(value):
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def get_json(path, params=None):
    url = BASE + path + ("?" + urlencode(params) if params else "")
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "paper-trader-quant-research/1"})
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


@dataclass(frozen=True)
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def validate_rows(rows, start, end):
    if not isinstance(rows, list) or len(rows) != (end - start) // HOUR:
        raise ValueError("Incomplete hourly history; no gaps are filled or silently dropped")
    candles = []
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 7:
            raise ValueError("Malformed kline")
        stamp = int(row[0])
        if stamp != start + index * HOUR or int(row[6]) != stamp + HOUR - 1:
            raise ValueError("Unexpected timestamps, gap, duplicate, or non-hourly candle")
        o, h, low, close, volume = map(float, row[1:6])
        if not all(math.isfinite(x) and x > 0 for x in (o, h, low, close)) or not (
                math.isfinite(volume) and volume >= 0 and low <= min(o, close) <= max(o, close) <= h):
            raise ValueError("Invalid OHLCV")
        candles.append(Candle(stamp, o, h, low, close, volume))
    return candles


def history(symbol, start, end, directory, offline=False):
    path = directory / f"{symbol}-1h-{start}-{end}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["symbol"] != symbol or saved["start"] != start or saved["endExclusive"] != end:
            raise ValueError("Cache metadata mismatch")
        rows = saved["rows"]
    else:
        if offline:
            raise ValueError(f"Offline cache missing: {path}")
        cursor, rows = start, []
        while cursor < end:
            batch = get_json("/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": 1000,
                                               "startTime": cursor, "endTime": end - 1})
            if not isinstance(batch, list) or not batch or int(batch[0][0]) != cursor:
                raise ValueError(f"Missing historical data for {symbol} at {iso(cursor)}")
            rows.extend(batch)
            cursor = int(batch[-1][0]) + HOUR
            print(f"{symbol}: downloaded {len(rows)} candles through {iso(cursor)}", flush=True)
        validate_rows(rows, start, end)
        write_json(path, {"source": BASE + "/api/v3/klines", "fetchedAt": iso(int(time.time() * 1000)),
                          "symbol": symbol, "interval": "1h", "start": start, "endExclusive": end, "rows": rows})
    candles = validate_rows(rows, start, end)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return candles, {"symbol": symbol, "path": str(path.resolve()), "sha256": digest, "candles": len(candles)}


def indicators(candles):
    """Each output uses only this candle and earlier candles, never execution-bar data."""
    result = [None] * len(candles)
    for i in range(50, len(candles)):
        prior = [c.close for c in candles[i - 20:i]]
        mean, deviation = statistics.mean(prior), statistics.pstdev(prior)
        close = candles[i].close
        result[i] = {
            "close": close,
            "sma5": statistics.mean(c.close for c in candles[i - 4:i + 1]),
            "sma20": statistics.mean(c.close for c in candles[i - 19:i + 1]),
            "sma50": statistics.mean(c.close for c in candles[i - 49:i + 1]),
            "return24h": close / candles[i - 24].close - 1,
            "return6h": close / candles[i - 6].close - 1,
            "priorHigh20": max(c.high for c in candles[i - 20:i]),
            "priorLow10": min(c.low for c in candles[i - 10:i]),
            "priorMean20": mean,
            "z": (close - mean) / deviation if deviation > 0 else 0.0,
        }
    return result


def entry(strategy, features, previous):
    if strategy == "trend_fast":
        return features["close"] > features["sma5"] > features["sma20"] and features["return6h"] > 0
    if strategy in ("trend_proxy", "trend_no_24h"):
        return (features["close"] > features["sma20"] > features["sma50"]
                and (strategy == "trend_no_24h" or features["return24h"] > 0))
    if strategy == "breakout":
        return features["close"] > features["priorHigh20"]
    if strategy == "mean_reversion":
        return previous["z"] <= -2 < features["z"]
    return strategy == "buy_hold"


def exit_signal(strategy, features, held_hours):
    if strategy == "trend_fast":
        return features["close"] < features["sma5"] and features["return6h"] < 0
    if strategy in ("trend_proxy", "trend_no_24h"):
        return features["close"] < features["sma20"] and features["return24h"] < 0
    if strategy == "breakout":
        return features["close"] < features["priorLow10"]
    if strategy == "mean_reversion":
        return features["close"] >= features["priorMean20"] or held_hours >= 24
    return False


@dataclass
class Position:
    quantity: Decimal = D(0)
    cost: Decimal = D(0)
    entry_fees: Decimal = D(0)
    entered: int = 0
    closing: bool = False
    round_pnl: Decimal = D(0)


class Account:
    def __init__(self, fee=FEE, risk_policy="legacy-v1"):
        if risk_policy not in RISK_POLICIES:
            raise ValueError("Unknown risk policy")
        self.risk_policy = risk_policy
        self.cash, self.fees, self.realized = CAPITAL, D(0), D(0)
        self.positions = {s: Position() for s in SYMBOLS}
        self.day, self.daily_pnl = None, D(0)
        self.fee = fee
        self.trades, self.closed_rounds = [], []
        self.dust, self.dust_cost = {s: D(0) for s in SYMBOLS}, D(0)

    def roll_day(self, stamp):
        day = stamp // (24 * HOUR)
        if day != self.day:
            self.day, self.daily_pnl = day, D(0)

    def exposure(self, marks):
        return sum(((p.quantity + self.dust[s]) * marks[s] for s, p in self.positions.items()), D(0))

    def equity(self, marks):
        return self.cash + self.exposure(marks)

    def fill(self, side, symbol, price, marks, stamp, signal_time):
        """Decimal accounting; V2 allows reducing sells and checks marked equity on BUY."""
        if side not in ("BUY", "SELL") or price <= 0:
            raise ValueError("Invalid order")
        self.roll_day(stamp)
        if self.daily_pnl <= -DAILY_LOSS and (side == "BUY" or self.risk_policy == "legacy-v1"):
            return False
        position = self.positions[symbol]
        if side == "BUY":
            if self.risk_policy == "reduce-only-v2" and self.equity(marks) - ORDER * self.fee <= CAPITAL - D("3"):
                return False
            if position.quantity or self.cash < ORDER * (1 + self.fee) or self.exposure(marks) + ORDER > EXPOSURE:
                return False
            gross, fee = ORDER, ORDER * self.fee
            quantity = gross / price
            position.quantity, position.cost, position.entry_fees = quantity, gross, fee
            position.entered, position.closing, position.round_pnl = stamp, False, D(0)
            self.cash -= gross + fee
            realized = D(0)
        else:
            if not position.quantity:
                return False
            # Sell notional is rounded down to the backend's six-decimal API precision.
            gross = min(ORDER, position.quantity * price).quantize(D("0.000001"), rounding=ROUND_DOWN)
            if not gross:
                return False
            quantity, fee = gross / price, gross * self.fee
            fraction = quantity / position.quantity
            cost, entry_fee = position.cost * fraction, position.entry_fees * fraction
            realized = gross - fee - cost - entry_fee
            position.quantity -= quantity
            position.cost -= cost
            position.entry_fees -= entry_fee
            position.round_pnl += realized
            self.cash += gross - fee
            self.realized += realized
            self.daily_pnl += realized
            # Residual below $0.000001 is untradeable under the API; retain it as dust
            # separately rather than resetting its accounting or emitting zero sells.
            if position.quantity * price < D("0.000001"):
                self.closed_rounds.append(float(position.round_pnl))
                dust_value = position.quantity * price
                self.dust[symbol] += position.quantity
                self.dust_cost += position.cost + position.entry_fees
                self.positions[symbol] = Position()
                assert dust_value >= 0
        self.fees += fee
        self.trades.append({"time": iso(stamp), "signalCandleOpen": iso(signal_time), "action": side,
                            "symbol": symbol, "price": str(price), "amountUsd": str(gross),
                            "feeUsd": str(fee), "realizedPnl": str(realized)})
        assert self.cash >= 0 and all(p.quantity >= 0 for p in self.positions.values())
        return True


def simulate(strategy, candles, features, start, end, slippage, risk_policy="legacy-v1"):
    if strategy not in STRATEGIES + EXPERIMENTAL_STRATEGIES:
        raise ValueError("Unknown strategy")
    account = Account(risk_policy=risk_policy)
    indices = [i for i, c in enumerate(candles[SYMBOLS[0]]) if start <= c.time < end]
    if not indices or indices[0] < 52:
        raise ValueError("Evaluation period needs at least 52 warm-up candles")
    curve, daily = [], {}
    peak, max_drawdown, exposure_sum, hours_in_market = float(CAPITAL), 0.0, 0.0, 0
    blocked_hours, daily_blocked_hours, equity_blocked_hours = 0, 0, 0
    eligible_entries, buy_gate_hours, no_buy_streak, longest_no_buy = 0, 0, 0, 0
    last_buy_symbol = None
    for i in indices:
        stamp = candles[SYMBOLS[0]][i].time
        account.roll_day(stamp)
        # Only the previous CLOSED candle can generate a signal for this open.
        signal = {s: features[s][i - 1] for s in SYMBOLS}
        candidates = [s for s in SYMBOLS if entry(strategy, signal[s], features[s][i - 2])]
        eligible_entries += len(candidates)
        buy_gate_hours += bool(candidates)
        opens = {s: D(str(candles[s][i].open)) for s in SYMBOLS}
        risk_exit = risk_policy == "reduce-only-v2" and (
            account.equity(opens) <= CAPITAL - D("3") or account.daily_pnl <= -DAILY_LOSS)
        for s, position in account.positions.items():
            if position.quantity and (risk_exit or exit_signal(strategy, signal[s], (stamp - position.entered) // HOUR)):
                position.closing = True  # Complete a capped exit even if its signal later disappears.
        closing = [s for s in SYMBOLS if account.positions[s].closing]
        bought = False
        daily_blocked = account.daily_pnl <= -DAILY_LOSS
        equity_blocked = risk_policy == "reduce-only-v2" and account.equity(opens) - ORDER * FEE <= CAPITAL - D("3")
        daily_blocked_hours += daily_blocked
        equity_blocked_hours += equity_blocked
        if daily_blocked or equity_blocked:
            blocked_hours += 1
        legacy_blocked = daily_blocked and risk_policy == "legacy-v1"
        if closing and not legacy_blocked:
            # V2 uses BTC first; preserve V1's oldest-pending-exit scheduler.
            symbol = closing[0] if risk_policy == "reduce-only-v2" else min(closing, key=lambda s: (account.positions[s].entered, s))
            price = opens[symbol] * (1 - slippage)
            account.fill("SELL", symbol, price, {**opens, symbol: price}, stamp, candles[symbol][i - 1].time)
        elif not legacy_blocked:
            candidates = [s for s in candidates if not account.positions[s].quantity]
            # Deterministic alternation avoids always choosing BTC when both signal together.
            if risk_policy == "legacy-v1":
                candidates.sort(key=lambda s: (s == last_buy_symbol, s))
            if candidates:
                symbol = candidates[0]
                price = opens[symbol] * (1 + slippage)
                bought = account.fill("BUY", symbol, price, {**opens, symbol: price}, stamp, candles[symbol][i - 1].time)
                if bought:
                    last_buy_symbol = symbol
        no_buy_streak = 0 if bought else no_buy_streak + 1
        longest_no_buy = max(longest_no_buy, no_buy_streak)
        marks = {s: D(str(candles[s][i].close)) for s in SYMBOLS}
        dust = sum((q * marks[s] for s, q in account.dust.items()), D(0))
        exposure = account.exposure(marks)
        equity = float(account.equity(marks))
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 100 * (peak - equity) / peak)
        exposure_sum += float(exposure)
        hours_in_market += any(p.quantity for p in account.positions.values())
        curve.append({"time": iso(stamp + HOUR), "equity": equity, "exposure": float(exposure)})
        daily[stamp // (24 * HOUR)] = equity
    daily_values = [float(CAPITAL), *daily.values()]
    returns = [b / a - 1 for a, b in zip(daily_values, daily_values[1:])]
    std = statistics.stdev(returns) if len(returns) > 1 else 0
    sharpe = statistics.mean(returns) / std * math.sqrt(365) if std else None
    end_exposure = curve[-1]["exposure"]
    end_equity = curve[-1]["equity"]
    remaining_cost = sum((p.cost + p.entry_fees for p in account.positions.values()), D(0)) + account.dust_cost
    unrealized = account.exposure(marks) - remaining_cost
    if abs(account.equity(marks) - (CAPITAL + account.realized + unrealized)) > D("0.000000000001"):
        raise ArithmeticError("Cash/position ledger does not reconcile to realized and unrealized PnL")
    # Sensitivity only: approximate disposal cost, not a fictional executed final liquidation.
    estimated_net_equity = float(account.cash) + end_exposure * float((1 - slippage) * (1 - FEE))
    rounds = account.closed_rounds
    gross_wins, gross_losses = sum(x for x in rounds if x > 0), -sum(x for x in rounds if x < 0)
    summary = {
        "strategy": strategy, "start": iso(start), "endExclusive": iso(end), "hours": len(indices),
        "riskPolicy": risk_policy,
        "initialEquity": float(CAPITAL), "endEquity": end_equity, "pnlUsd": end_equity - float(CAPITAL),
        "returnPct": (end_equity / float(CAPITAL) - 1) * 100,
        "estimatedNetExitEquity": estimated_net_equity,
        "maxDrawdownPct": max_drawdown, "dailySharpeZeroCashRate": sharpe,
        "feesUsd": float(account.fees), "realizedPnlUsd": float(account.realized),
        "unrealizedPnlUsd": float(unrealized),
        "openExposureUsd": end_exposure, "averageExposureUsd": exposure_sum / len(indices),
        "timeInMarketPct": hours_in_market / len(indices) * 100,
        "buyFills": sum(t["action"] == "BUY" for t in account.trades),
        "sellFills": sum(t["action"] == "SELL" for t in account.trades),
        "closedRoundTrips": len(rounds), "winRatePct": 100 * sum(x > 0 for x in rounds) / len(rounds) if rounds else None,
        "profitFactor": gross_wins / gross_losses if gross_losses else None,
        "entrySignalSymbolHours": eligible_entries, "hoursWithEntrySignalPct": 100 * buy_gate_hours / len(indices),
        "longestNoBuyHours": longest_no_buy, "dailyLossBlockedHours": daily_blocked_hours,
        "equityLossBlockedHours": equity_blocked_hours, "riskBlockedHours": blocked_hours,
        "untradeableDustValueUsd": float(dust), "slippageBpsPerSide": float(slippage * 10000),
    }
    return {"summary": summary, "equityCurve": curve, "trades": account.trades}


def report(result):
    lines = ["# BTC/ETH spot strategy comparison", "", f"Generated: {result['generatedAt']}", "",
             "Research only. No model calls, portfolio API access, state changes, or strategy activation.", "",
             "## Method", "", *[f"- {x}" for x in result["assumptions"]], "", "## Fixed candidate rules", ""]
    selected = result["parameters"].get("strategies", STRATEGIES)
    lines.extend(f"- **{name}**: {RULES[name]}" for name in selected)
    for period in ("reference", "holdout"):
        lines += ["", f"## {period.title()} — 5 bps slippage per side", "",
                  "| Strategy | Return | PnL $ | Max drawdown | BUY / SELL fills | Fees $ | Avg exposure $ | Entry-signal hours |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for run in result["runs"]:
            s = run["summary"]
            if run["period"] == period and s["slippageBpsPerSide"] == 5:
                lines.append(f"| {s['strategy']} | {s['returnPct']:.2f}% | {s['pnlUsd']:.4f} | {s['maxDrawdownPct']:.2f}% | "
                             f"{s['buyFills']} / {s['sellFills']} | {s['feesUsd']:.4f} | {s['averageExposureUsd']:.2f} | "
                             f"{s['hoursWithEntrySignalPct']:.1f}% |")
    lines += ["", "## Holdout execution-cost sensitivity", "",
              "| Strategy | Return: 0 bps | Return: 5 bps | Return: 10 bps |",
              "| --- | ---: | ---: | ---: |"]
    for name in selected:
        values = {r["summary"]["slippageBpsPerSide"]: r["summary"]["returnPct"] for r in result["runs"]
                  if r["period"] == "holdout" and r["summary"]["strategy"] == name}
        lines.append(f"| {name} | {values[0]:.2f}% | {values[5]:.2f}% | {values[10]:.2f}% |")
    lines += ["", "## Interpretation limits", "",
              "The holdout is a single chronological evaluation, not proof of future performance. "
              "Reference/holdout are window labels, not a guarantee that the data was previously unseen; "
              "new candidates tested on previously reviewed history need fresh forward evaluation. "
              "Using these results to adjust parameters turns this period into development data; "
              "a subsequent untouched period or forward paper run is then required.", "",
              "The current AI strategy cannot be replayed exactly. trend_proxy uses closed-candle prices "
              "instead of the live quote and omits subjective confidence/fee judgments. "
              "All active candidates use the same one-entry-per-symbol policy. "
              "Its output is a technical-rule proxy, not the deployed bot's historical PnL.", "",
              "Hourly close drawdown omits intrahour extremes. Next-hour opens approximate execution, "
              "not the actual minute-02 quote. The 0/5/10 bps scenarios model adverse spread/slippage, not measured fills. "
              "No hard intrabar stop is simulated. The mean-reversion 24-hour exit is a signal, and fills may be delayed "
              "by another exit or the order cap. The legacy-v1 risk policy also blocks sells after the daily loss limit; reduce-only-v2 allows reducing sells.", "",
              "Returns are on the whole $50 account including idle cash; average exposure differs across strategies. "
              "Buy-and-hold deploys $5 per coin, matching the candidates' entry sizing, not a fully invested $50 benchmark. "
              "Terminal positions remain marked to market; estimatedNetExitEquity in results.json separately estimates "
              "disposal costs. Dust below $0.000001 is retained and valued, but excluded from re-entry scheduling. "
              "Cash earns no interest; USDT is treated as USD without depeg or venue-default modeling.", "",
              "## Data provenance", "",
              "Source: [Binance public spot klines](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints). "
              "100 earlier candles are used only to warm indicators. Missing, duplicated, and invalid bars abort the run.", ""]
    for item in result["data"]:
        lines.append(f"- {item['symbol']}: {item['candles']} hourly candles; SHA-256 `{item['sha256']}`.")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-09-01")
    parser.add_argument("--split", default="2026-03-01")
    parser.add_argument("--end", default="2026-09-01", help="Exclusive UTC date")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/quant/candles"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/quant/comparison"))
    parser.add_argument("--offline", action="store_true", help="Use complete existing caches without network access")
    parser.add_argument("--risk-policy", choices=RISK_POLICIES, default="legacy-v1")
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES + EXPERIMENTAL_STRATEGIES, default=STRATEGIES)
    args = parser.parse_args()
    start, split, end = map(parse_date, (args.start, args.split, args.end))
    if not start < split < end:
        raise ValueError("Require start < split < end")
    server_ms = int(time.time() * 1000) if args.offline else int(get_json("/api/v3/time")["serverTime"])
    if end > server_ms // HOUR * HOUR:
        raise ValueError("Requested period contains future or unclosed candles")
    candles, metadata = {}, []
    for symbol in SYMBOLS:
        candles[symbol], meta = history(symbol, start - 100 * HOUR, end, args.cache_dir, args.offline)
        metadata.append(meta)
    features = {s: indicators(candles[s]) for s in SYMBOLS}
    result = {"generatedAt": iso(int(time.time() * 1000)), "data": metadata,
              "codeSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "parameters": {"start": args.start, "split": args.split, "endExclusive": args.end,
                             "riskPolicy": args.risk_policy, "strategies": list(args.strategies),
                             "rules": {s: RULES[s] for s in args.strategies}},
              "assumptions": [
                  f"Reference: {args.start} to {args.split}; holdout: {args.split} to {args.end} (end exclusive, UTC).",
                  "Candidate rules are declared before this run; no grid search or model training. Previously reviewed windows are development evidence, not new untouched holdouts.",
                  "$50 initial cash per strategy and per period; BTC/ETH spot, no shorting or leverage.",
                  "One $5 entry per symbol, no pyramiding; $20 marked buy exposure cap; one action per hour, exits first.",
                  "Signals use closed 1h candles; fills use next candle open with adverse slippage, never signal-bar close.",
                  "Fee 0.1% each side; slippage/spread scenarios 0, 5, 10 bps each side (base case 5).",
                  ("V2: $3 daily loss blocks entries, reducing sells remain allowed; equity at/below $47 triggers capped liquidation, and BUY fees count against that floor. BTC wins ties."
                   if args.risk_policy == "reduce-only-v2" else
                   "Legacy V1: the UTC $3 daily realized-loss limit blocks both BUY and SELL; alternating entry priority."),
                  "The $47 V2 threshold triggers action only on sampled hourly opens; gaps and capped exits can overshoot it.",
                  "SELL notional capped at $5 and floored to six decimals; partial exits remain queued until complete.",
              ], "runs": []}
    for period, beginning, ending in (("reference", start, split), ("holdout", split, end)):
        for bps in (0, 5, 10):
            for strategy in args.strategies:
                run = simulate(strategy, candles, features, beginning, ending, D(bps) / 10000, args.risk_policy)
                run["period"] = period
                result["runs"].append(run)
                print(f"{period} {strategy} {bps}bps: return={run['summary']['returnPct']:.3f}%", flush=True)
    write_json(args.output_dir / "results.json", result)
    (args.output_dir / "report.md").write_text(report(result), encoding="utf-8")
    print(f"Report saved to {args.output_dir.resolve() / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
