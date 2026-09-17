"""Behavioral checks for research integrity and accounting; no network requests."""

from copy import deepcopy
from decimal import Decimal as D
import unittest

import backtest as bt


def series(count=180):
    return [bt.Candle(i * bt.HOUR, 100 + i / 10, 101 + i / 10,
                      99 + i / 10, 100 + i / 10, 100) for i in range(count)]


class DataTests(unittest.TestCase):
    def test_missing_duplicate_and_invalid_ohlc_are_rejected(self):
        rows = [[i * bt.HOUR, "100", "101", "99", "100", "1", (i + 1) * bt.HOUR - 1]
                for i in range(3)]
        self.assertEqual(len(bt.validate_rows(rows, 0, 3 * bt.HOUR)), 3)
        cases = [rows[:-1], [rows[0], rows[0], rows[2]], deepcopy(rows), deepcopy(rows)]
        cases[2][1][2] = "90"
        cases[3][1][4] = "NaN"
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                bt.validate_rows(case, 0, 3 * bt.HOUR)

    def test_indicators_do_not_change_when_future_prices_change(self):
        original = series()
        modified = original[:121] + [bt.Candle(c.time, 500, 600, 400, 550, 1) for c in original[121:]]
        self.assertEqual(bt.indicators(original)[:121], bt.indicators(modified)[:121])

    def test_breakout_excludes_signal_candle_from_reference_high(self):
        candles = series()
        candles[100] = bt.Candle(100 * bt.HOUR, 110, 501, 100, 500, 1)
        f = bt.indicators(candles)
        self.assertEqual(f[100]["priorHigh20"], candles[99].high)
        self.assertTrue(bt.entry("breakout", f[100], f[99]))

    def test_relaxed_trend_removes_only_return_entry_gate(self):
        f = {"close": 110, "sma20": 105, "sma50": 100, "return24h": -0.01}
        self.assertFalse(bt.entry("trend_proxy", f, f))
        self.assertTrue(bt.entry("trend_no_24h", f, f))
        f["close"] = 99
        self.assertEqual(bt.exit_signal("trend_proxy", f, 1), bt.exit_signal("trend_no_24h", f, 1))

    def test_mean_reversion_waits_for_reentry_and_exits_on_time(self):
        self.assertFalse(bt.entry("mean_reversion", {"z": -3}, {"z": -2.5}))
        self.assertTrue(bt.entry("mean_reversion", {"z": -1.9}, {"z": -2.5}))
        f = {"close": 90, "priorMean20": 100}
        self.assertFalse(bt.exit_signal("mean_reversion", f, 23))
        self.assertTrue(bt.exit_signal("mean_reversion", f, 24))

    def test_fast_candidate_can_enter_early_recovery_but_requires_six_hour_momentum(self):
        f = {"close": 103, "sma5": 102, "sma20": 101, "sma50": 105,
             "return6h": 0.01, "return24h": -0.02}
        self.assertTrue(bt.entry("trend_fast", f, f))
        self.assertFalse(bt.entry("trend_proxy", f, f))
        f["return6h"] = 0
        self.assertFalse(bt.entry("trend_fast", f, f))
        f.update(close=100, return6h=-0.01)
        self.assertTrue(bt.exit_signal("trend_fast", f, 1))


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.account = bt.Account()
        self.marks = {s: D(100) for s in bt.SYMBOLS}

    def test_round_trip_realized_pnl_includes_both_fees(self):
        account = self.account
        account.fill("BUY", "BTCUSDT", D(100), self.marks, 0, -bt.HOUR)
        self.assertEqual(account.cash, D("44.995"))
        self.assertEqual(account.positions["BTCUSDT"].quantity, D("0.05"))
        marks = {**self.marks, "BTCUSDT": D(90)}
        account.fill("SELL", "BTCUSDT", D(90), marks, bt.HOUR, 0)
        self.assertEqual(account.cash, D("49.4905"))
        self.assertEqual(account.realized, D("-0.5095"))
        self.assertEqual(account.fees, D("0.0095"))
        self.assertEqual(len(account.closed_rounds), 1)

    def test_appreciated_position_requires_partial_exits_below_order_cap(self):
        account = self.account
        account.fill("BUY", "BTCUSDT", D(100), self.marks, 0, -bt.HOUR)
        marks = {**self.marks, "BTCUSDT": D(120)}
        account.fill("SELL", "BTCUSDT", D(120), marks, bt.HOUR, 0)
        self.assertGreater(account.positions["BTCUSDT"].quantity, 0)
        account.fill("SELL", "BTCUSDT", D(120), marks, 2 * bt.HOUR, bt.HOUR)
        self.assertTrue(all(D(t["amountUsd"]) <= 5 for t in account.trades))
        self.assertLess(abs(account.equity(marks) - D("50.989")), D("0.000001"))
        self.assertEqual(len(account.closed_rounds), 1)

    def test_daily_loss_blocks_both_sides_and_resets_at_midnight(self):
        account = self.account
        account.fill("BUY", "BTCUSDT", D(100), self.marks, 0, -bt.HOUR)
        account.fill("BUY", "ETHUSDT", D(100), self.marks, bt.HOUR, 0)
        marks = {**self.marks, "BTCUSDT": D(10)}
        account.fill("SELL", "BTCUSDT", D(10), marks, 2 * bt.HOUR, bt.HOUR)
        self.assertLess(account.daily_pnl, -3)
        self.assertFalse(account.fill("BUY", "BTCUSDT", D(10), marks, 3 * bt.HOUR, 2 * bt.HOUR))
        self.assertFalse(account.fill("SELL", "ETHUSDT", D(100), marks, 3 * bt.HOUR, 2 * bt.HOUR))
        self.assertTrue(account.fill("SELL", "ETHUSDT", D(100), marks, 24 * bt.HOUR, 23 * bt.HOUR))

    def test_no_pyramiding_shorting_or_buy_without_cash(self):
        account = self.account
        self.assertFalse(account.fill("SELL", "BTCUSDT", D(100), self.marks, 0, -bt.HOUR))
        self.assertTrue(account.fill("BUY", "BTCUSDT", D(100), self.marks, 0, -bt.HOUR))
        self.assertFalse(account.fill("BUY", "BTCUSDT", D(100), self.marks, bt.HOUR, 0))
        account.cash = D(5)
        self.assertFalse(account.fill("BUY", "ETHUSDT", D(100), self.marks, 2 * bt.HOUR, bt.HOUR))

    def test_marked_exposure_blocks_buy_but_allows_sell(self):
        account = self.account
        account.fill("BUY", "BTCUSDT", D(100), self.marks, 0, -bt.HOUR)
        marks = {**self.marks, "BTCUSDT": D(500)}
        self.assertFalse(account.fill("BUY", "ETHUSDT", D(100), marks, bt.HOUR, 0))
        self.assertTrue(account.fill("SELL", "BTCUSDT", D(500), marks, bt.HOUR, 0))


class SimulationTests(unittest.TestCase):
    def inputs(self):
        data = {s: series() for s in bt.SYMBOLS}
        features = {s: bt.indicators(data[s]) for s in bt.SYMBOLS}
        return data, features

    def test_signal_executes_only_at_next_open_with_adverse_slippage(self):
        data, features = self.inputs()
        for s in bt.SYMBOLS:
            data[s][100] = bt.Candle(100 * bt.HOUR, 200, 201, 109, 110, 1)
        run = bt.simulate("trend_proxy", data, features, 100 * bt.HOUR, 105 * bt.HOUR, D("0.001"))
        first = run["trades"][0]
        self.assertEqual(first["time"], bt.iso(100 * bt.HOUR))
        self.assertEqual(first["signalCandleOpen"], bt.iso(99 * bt.HOUR))
        self.assertEqual(D(first["price"]), D("200.2"))

    def test_at_most_one_action_per_hour_and_holdout_starts_flat(self):
        data, features = self.inputs()
        run = bt.simulate("buy_hold", data, features, 100 * bt.HOUR, 150 * bt.HOUR, D(0))
        self.assertEqual(len(run["trades"]), 2)
        self.assertEqual(len({t["time"] for t in run["trades"]}), 2)
        later = bt.simulate("buy_hold", data, features, 150 * bt.HOUR, 160 * bt.HOUR, D(0))
        self.assertEqual(later["summary"]["initialEquity"], 50)
        self.assertEqual(later["trades"][0]["time"], bt.iso(150 * bt.HOUR))

    def test_future_changes_do_not_change_earlier_trades(self):
        original, f1 = self.inputs()
        changed = {s: original[s][:121] + [bt.Candle(c.time, 50, 55, 45, 50, 1) for c in original[s][121:]]
                   for s in bt.SYMBOLS}
        f2 = {s: bt.indicators(changed[s]) for s in bt.SYMBOLS}
        for strategy in bt.STRATEGIES + bt.EXPERIMENTAL_STRATEGIES:
            for risk_policy in bt.RISK_POLICIES:
                with self.subTest(strategy=strategy, risk_policy=risk_policy):
                    a = bt.simulate(strategy, original, f1, 100 * bt.HOUR, 170 * bt.HOUR, D("0.0005"), risk_policy)
                    b = bt.simulate(strategy, changed, f2, 100 * bt.HOUR, 170 * bt.HOUR, D("0.0005"), risk_policy)
                    cutoff = bt.iso(121 * bt.HOUR)
                    self.assertEqual([t for t in a["trades"] if t["time"] < cutoff],
                                     [t for t in b["trades"] if t["time"] < cutoff])

    def test_partial_exit_stays_queued_and_takes_priority_over_new_entry(self):
        data, features = self.inputs()
        features["BTCUSDT"][100] = {**features["BTCUSDT"][100], "close": 90, "return24h": -0.1}
        for i in (101, 102):
            data["BTCUSDT"][i] = bt.Candle(i * bt.HOUR, 120, 121, 119, 120, 1)
        run = bt.simulate("trend_proxy", data, features, 100 * bt.HOUR, 104 * bt.HOUR, D(0))
        self.assertEqual([(t["action"], t["symbol"]) for t in run["trades"]],
                         [("BUY", "BTCUSDT"), ("SELL", "BTCUSDT"), ("SELL", "BTCUSDT"), ("BUY", "ETHUSDT")])
        self.assertTrue(all(D(t["amountUsd"]) <= 5 for t in run["trades"]))

    def test_flat_price_buy_hold_loses_fees_and_slippage_and_cash_is_flat(self):
        data = {s: [bt.Candle(i * bt.HOUR, 100, 101, 99, 100, 1) for i in range(180)] for s in bt.SYMBOLS}
        features = {s: bt.indicators(data[s]) for s in bt.SYMBOLS}
        zero = bt.simulate("buy_hold", data, features, 100 * bt.HOUR, 170 * bt.HOUR, D(0))
        cost = bt.simulate("buy_hold", data, features, 100 * bt.HOUR, 170 * bt.HOUR, D("0.001"))
        cash = bt.simulate("cash", data, features, 100 * bt.HOUR, 170 * bt.HOUR, D("0.001"))
        self.assertLess(cost["summary"]["endEquity"], zero["summary"]["endEquity"])
        self.assertLess(zero["summary"]["endEquity"], 50)
        self.assertEqual(cash["summary"]["endEquity"], 50)
        self.assertEqual(cash["summary"]["maxDrawdownPct"], 0)


class V2RiskTests(unittest.TestCase):
    def test_reducing_sell_survives_daily_loss_but_buy_does_not(self):
        account = bt.Account(risk_policy="reduce-only-v2")
        marks = {s: D(100) for s in bt.SYMBOLS}
        account.fill("BUY", "BTCUSDT", D(100), marks, 0, -bt.HOUR)
        account.fill("BUY", "ETHUSDT", D(100), marks, bt.HOUR, 0)
        marks["BTCUSDT"] = D(10)
        account.fill("SELL", "BTCUSDT", D(10), marks, 2 * bt.HOUR, bt.HOUR)
        self.assertLess(account.daily_pnl, -3)
        self.assertFalse(account.fill("BUY", "BTCUSDT", D(10), marks, 3 * bt.HOUR, 2 * bt.HOUR))
        self.assertTrue(account.fill("SELL", "ETHUSDT", D(100), marks, 3 * bt.HOUR, 2 * bt.HOUR))

    def test_entry_fee_counts_against_equity_floor_and_floor_persists_next_day(self):
        account = bt.Account(risk_policy="reduce-only-v2")
        marks = {s: D(100) for s in bt.SYMBOLS}
        account.cash = D("47.005")
        self.assertFalse(account.fill("BUY", "BTCUSDT", D(100), marks, 0, -bt.HOUR))
        self.assertFalse(account.fill("BUY", "BTCUSDT", D(100), marks, 24 * bt.HOUR, 23 * bt.HOUR))
        account.cash += D("0.000001")
        self.assertTrue(account.fill("BUY", "BTCUSDT", D(100), marks, 24 * bt.HOUR, 23 * bt.HOUR))

    def test_price_gap_forces_both_exits_one_per_hour_and_can_overshoot_floor(self):
        data = {s: [bt.Candle(i * bt.HOUR, p, p + 1, p - 1, p, 1)
                    for i in range(80) for p in [100 if i < 60 else 10]] for s in bt.SYMBOLS}
        features = {s: bt.indicators(data[s]) for s in bt.SYMBOLS}
        run = bt.simulate("buy_hold", data, features, 52 * bt.HOUR, 70 * bt.HOUR, D(0), "reduce-only-v2")
        self.assertEqual([(t["action"], t["symbol"]) for t in run["trades"]],
                         [("BUY", "BTCUSDT"), ("BUY", "ETHUSDT"), ("SELL", "BTCUSDT"), ("SELL", "ETHUSDT")])
        self.assertEqual([t["time"] for t in run["trades"][2:]], [bt.iso(60 * bt.HOUR), bt.iso(61 * bt.HOUR)])
        self.assertLess(run["summary"]["endEquity"], 47)
        self.assertEqual(run["summary"]["closedRoundTrips"], 2)

    def test_v2_entry_priority_is_btc_even_after_a_btc_exit(self):
        data = {s: series() for s in bt.SYMBOLS}
        features = {s: bt.indicators(data[s]) for s in bt.SYMBOLS}
        features["BTCUSDT"][100].update(close=90, return24h=-0.1)
        data["BTCUSDT"][101] = bt.Candle(101 * bt.HOUR, 100, 101, 99, 100, 1)
        run = bt.simulate("trend_proxy", data, features, 100 * bt.HOUR, 103 * bt.HOUR, D(0), "reduce-only-v2")
        self.assertEqual([(t["action"], t["symbol"]) for t in run["trades"]],
                         [("BUY", "BTCUSDT"), ("SELL", "BTCUSDT"), ("BUY", "BTCUSDT")])


if __name__ == "__main__":
    unittest.main()
