"""Small analytic checks for time attribution, partial fills and dependent sampling."""

import unittest

import numpy as np

from existing_evidence import Ledger, circular_block_mean_ci, daily_returns, monthly_returns, return_diagnostics


class EvidenceTests(unittest.TestCase):
    def test_midnight_close_belongs_to_previous_day_and_includes_first_return(self):
        curve = [{"time": "2026-03-01T01:00:00Z", "equity": 49},
                 {"time": "2026-03-02T00:00:00Z", "equity": 55},
                 {"time": "2026-03-02T01:00:00Z", "equity": 54},
                 {"time": "2026-03-03T00:00:00Z", "equity": 44}]
        daily = daily_returns(curve)
        self.assertEqual([r["date"] for r in daily], ["2026-03-01", "2026-03-02"])
        self.assertAlmostEqual(daily[0]["return"], .1)
        self.assertAlmostEqual(daily[1]["return"], -.2)
        self.assertAlmostEqual(np.prod([1 + x["return"] for x in daily]), 44 / 50)
        self.assertAlmostEqual(monthly_returns(daily)[0]["returnPct"], -12)

    def test_partial_exit_allocates_entry_fees_once_and_completes_one_round(self):
        ledger = Ledger()
        for time, action, price, pnl in [("00", "BUY", "1", "0"),
                                         ("01", "SELL", "2", "2.4925"),
                                         ("02", "SELL", "2", "2.4925")]:
            ledger.fill({"time": f"2026-03-01T{time}:00:00Z", "action": action,
                         "symbol": "BTCUSDT", "price": price, "amountUsd": "5",
                         "feeUsd": "0.005", "realizedPnl": pnl})
        self.assertAlmostEqual(float(ledger.cash), 54.985)
        self.assertEqual(len(ledger.completed), 1)
        self.assertAlmostEqual(ledger.completed[0]["netPnlUsd"], 4.985)
        self.assertEqual(ledger.completed[0]["holdingHours"], 2)
        self.assertEqual(ledger.completed[0]["sellFills"], 2)
        self.assertEqual(len(ledger.positions), 0)

    def test_bootstrap_constant_and_paired_identity(self):
        for length in (1, 7, 14):
            result = circular_block_mean_ci(np.full(28, 0.0002), length, resamples=3000)
            np.testing.assert_allclose(result["percentile95MeanDailyExcessBps"], [2, 2])
            self.assertAlmostEqual(result["bootstrapMeanStdErrorBps"], 0)
            zero = circular_block_mean_ci(np.arange(28) - np.arange(28), length, resamples=3000)
            self.assertEqual(zero["percentile95MeanDailyExcessBps"], [0, 0])

    def test_blocks_preserve_positive_serial_dependence_and_are_reproducible(self):
        # Alternating long positive/negative regimes: block sampling should have
        # wider uncertainty than independently resampling individual observations.
        data = np.repeat([.01, -.01, .01, -.01], 35)
        iid = circular_block_mean_ci(data, 1)
        block = circular_block_mean_ci(data, 7)
        self.assertGreater(block["bootstrapMeanStdErrorBps"], iid["bootstrapMeanStdErrorBps"] * 1.8)
        self.assertEqual(block, circular_block_mean_ci(data, 7))

    def test_cash_sharpe_is_undefined_and_short_hac_suppressed(self):
        cash = return_diagnostics(np.zeros(184))
        self.assertIsNone(cash["naiveDailySharpeAnnualized365"])
        self.assertIsNone(cash["hacSharpeAnnualized365"])
        self.assertIsNone(return_diagnostics([.01, -.01] * 4)["hacSharpeAnnualized365"])


if __name__ == "__main__":
    unittest.main()
