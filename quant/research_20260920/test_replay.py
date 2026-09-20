"""Research invariants: causal inputs, exit scheduling, risk state and accounting."""
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json

import replay as r


def policy_data(count=90, price='100'):
    """Already observed features for controlled portfolio-state experiments."""
    return {s: [
        {'time': i*r.HOUR, 'open': D(price), 'close': D(price),
         'features': {'sma20': '90', 'sma50': '80', 'return24hPct': '1'}}
        for i in range(count)] for s in r.policy.UNIVERSES['five']}


def set_bar(data, i, price, symbols=None, close=None, **features):
    for s in symbols or data:
        data[s][i]['open'] = D(str(price))
        data[s][i]['close'] = D(str(price if close is None else close))
        data[s][i]['features'].update({k: str(v) for k, v in features.items()})


def write_caches(root, count=100, perturb_from=None):
    directory = root/'candles'
    directory.mkdir()
    for s in r.policy.UNIVERSES['five']:
        rows = []
        for i in range(count):
            o = D(100) + D(i)/10
            c = o
            if perturb_from is not None and i >= perturb_from:
                # Preserve the decision open of the first perturbed bar, change
                # its not-yet-observable close and all future prices drastically.
                o = o if i == perturb_from else D(400)
                c = D(400)
            rows.append([i*r.HOUR, str(o), str(max(o, c)+1),
                         str(min(o, c)-1), str(c), '10', (i+1)*r.HOUR-1])
        (directory/(s+'-1h-test.json')).write_text(json.dumps({
            'symbol': s, 'interval': '1h', 'start': 0,
            'endExclusive': count*r.HOUR, 'rows': rows}), encoding='utf-8')


class InputAndCausalityTests(unittest.TestCase):
    def test_loader_lags_indicators_and_current_close_cannot_change_current_trade(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            write_caches(Path(a))
            write_caches(Path(b), perturb_from=60)
            with patch.object(r, 'OUT', Path(a)):
                original = r.load_inputs()
            with patch.object(r, 'OUT', Path(b)):
                changed = r.load_inputs()
        expected = sum((D(100)+D(i)/10 for i in range(40, 60)), D(0))/20
        self.assertEqual(D(original['BTCUSDT'][60]['features']['sma20']), expected)
        self.assertEqual(original['BTCUSDT'][60]['features'], changed['BTCUSDT'][60]['features'])
        self.assertNotEqual(original['BTCUSDT'][61]['features'], changed['BTCUSDT'][61]['features'])
        for universe in r.UNIVERSES:
            for variant in r.VARIANTS:
                with self.subTest(universe=universe, variant=variant):
                    x = r.simulate(original, universe, variant, 60*r.HOUR, 90*r.HOUR)
                    y = r.simulate(changed, universe, variant, 60*r.HOUR, 90*r.HOUR)
                    stamp = r.iso(60*r.HOUR)
                    self.assertEqual([t for t in x['trades'] if t['time'] <= stamp],
                                     [t for t in y['trades'] if t['time'] <= stamp])

    def test_future_bars_cannot_change_prior_trades_for_all_variants(self):
        data = policy_data()
        changed = deepcopy(data)
        for i in range(65, 90):
            set_bar(changed, i, 10, sma20=200, sma50=250, return24hPct=-50)
        for universe in r.UNIVERSES:
            for variant in r.VARIANTS:
                with self.subTest(universe=universe, variant=variant):
                    x = r.simulate(data, universe, variant, 50*r.HOUR, 85*r.HOUR)
                    y = r.simulate(changed, universe, variant, 50*r.HOUR, 85*r.HOUR)
                    cutoff = r.iso(65*r.HOUR)
                    self.assertEqual([t for t in x['trades'] if t['time'] < cutoff],
                                     [t for t in y['trades'] if t['time'] < cutoff])

    def test_invalid_gap_and_ohlc_abort_load(self):
        for corruption in ('gap', 'ohlc', 'infinite_high', 'infinite_volume', 'wrong_symbol', 'wrong_interval'):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_caches(root)
                path = next((root/'candles').glob('BTC*'))
                doc = json.loads(path.read_text())
                if corruption == 'gap':
                    doc['rows'][55][0] += r.HOUR
                elif corruption == 'ohlc':
                    doc['rows'][55][2] = '1'
                elif corruption == 'infinite_high':
                    doc['rows'][55][2] = 'Infinity'
                elif corruption == 'infinite_volume':
                    doc['rows'][55][5] = 'Infinity'
                elif corruption == 'wrong_symbol':
                    doc['symbol'] = 'WRONGUSDT'
                elif corruption == 'wrong_interval':
                    doc['interval'] = '4h'
                path.write_text(json.dumps(doc))
                with patch.object(r, 'OUT', root), self.assertRaises(AssertionError):
                    r.load_inputs()


class ExitSchedulingTests(unittest.TestCase):
    def test_batched_partial_exits_finish_both_coins_same_hour_without_buy(self):
        data = policy_data(price='110')
        set_bar(data, 52, 120, sma20=125, return24hPct=-1)
        # At 53 the trend signal disappears. Baseline must retain its queue;
        # batch must not open a new trade in the previous liquidation hour.
        set_bar(data, 53, 120)
        baseline = r.simulate(data, 'two', 'baseline', 50*r.HOUR, 54*r.HOUR, 0)
        batch = r.simulate(data, 'two', 'batch_exit', 50*r.HOUR, 54*r.HOUR, 0)
        stamp = r.iso(52*r.HOUR)
        exits = [t for t in batch['trades'] if t['time'] == stamp]
        self.assertEqual([(t['action'], t['symbol']) for t in exits],
                         [('SELL', 'BTCUSDT'), ('SELL', 'BTCUSDT'),
                          ('SELL', 'ETHUSDT'), ('SELL', 'ETHUSDT')])
        self.assertTrue(all(D(t['amountUsd']) <= 5 for t in exits))
        self.assertEqual(len(batch['roundTrips']), 2)
        self.assertTrue(all(t['exit'] == stamp for t in batch['roundTrips']))
        self.assertEqual(batch['summary']['maxCompletedExitWaitHours'], 0)
        self.assertEqual(batch['summary']['exitBacklogHours'], 0)
        self.assertEqual([(t['action'],t['symbol']) for t in baseline['trades']
                          if t['time'] == r.iso(53*r.HOUR)], [('SELL','BTCUSDT')])
        self.assertGreater(baseline['summary']['exitBacklogHours'], 0)

    def test_adverse_sell_fill_clips_notional_to_held_quantity(self):
        ledger = r.Ledger(('BTCUSDT',))
        ledger.fill('BUY', 'BTCUSDT', '5', D(100), 0)
        self.assertTrue(ledger.fill('SELL', 'BTCUSDT', '5', D('99.95'), r.HOUR))
        self.assertEqual(D(ledger.trades[-1]['amountUsd']), D('4.997500'))
        self.assertGreaterEqual(ledger.pos['BTCUSDT']['qty'], 0)
        ledger.reconcile({'BTCUSDT': D(100)})

    def test_exit_backlog_does_not_leak_from_dust_into_a_fresh_entry(self):
        data = policy_data()
        for bar in data['ETHUSDT']:
            bar['features']['return24hPct'] = '-1'
        slip = D('0.0005')
        quantity = (D(5)/(D(100)*(1+slip))).quantize(r.Q, rounding=r.ROUND_DOWN)
        # Residual after selling is below MICRO at fill price but just above
        # MICRO at the observation open; an additional sell cannot execute.
        exit_open = (D('4.90000099975')/quantity)/(1-slip)
        set_bar(data, 51, exit_open, symbols=('BTCUSDT',), sma20=200, return24hPct=-1)
        for i in (52, 53):
            set_bar(data, i, 90, symbols=('BTCUSDT',), sma20=80, sma50=70, return24hPct=1)
        run = r.simulate(data, 'two', 'batch_exit', 50*r.HOUR, 54*r.HOUR, 5)
        self.assertEqual([(t['time'],t['action']) for t in run['trades']],
                         [(r.iso(50*r.HOUR),'BUY'), (r.iso(51*r.HOUR),'SELL'),
                          (r.iso(52*r.HOUR),'BUY')])
        self.assertEqual(run['summary']['exitBacklogHours'], 1)


class RiskStateTests(unittest.TestCase):
    def test_daily_loss_allows_reduction_blocks_entries_until_utc_reset(self):
        for variant in ('baseline', 'batch_exit'):
            with self.subTest(variant=variant):
                data = policy_data()
                ledger = r.Ledger(r.UNIVERSES['two'])
                ledger.fill('BUY', 'BTCUSDT', '5', D(100), 50*r.HOUR)
                ledger.daily_pnl = D(-3)
                ledger.trades.clear()  # Preserve seeded holdings, inspect replay actions only.
                with patch.object(r, 'Ledger', return_value=ledger):
                    result = r.simulate(data, 'two', variant, 50*r.HOUR, 74*r.HOUR, 0)
                self.assertEqual(result['trades'][0]['action'], 'SELL')
                self.assertEqual(result['trades'][0]['time'], r.iso(50*r.HOUR))
                self.assertTrue(all(t['action'] != 'BUY' for t in result['trades']
                                    if t['time'] < r.iso(72*r.HOUR)))
                self.assertEqual(next(t['time'] for t in result['trades'] if t['action']=='BUY'),
                                 r.iso(72*r.HOUR))
                self.assertEqual(result['summary']['equityBlockedHours'], 0)

    def test_peak_guard_latches_permanently_after_prices_recover(self):
        data = policy_data()
        set_bar(data, 52, 160)
        set_bar(data, 53, 120)
        for i in range(54, 70):
            set_bar(data, i, 200)
        for variant in ('peak_guard', 'batch_peak'):
            with self.subTest(variant=variant):
                result = r.simulate(data, 'two', variant, 50*r.HOUR, 70*r.HOUR, 0)
                self.assertEqual(result['summary']['firstLossHalt'], r.iso(53*r.HOUR))
                self.assertEqual(result['summary']['peakBlockedHours'], 17)
                self.assertEqual(result['summary']['buyFills'], 2)
                self.assertTrue(all(t['action']=='SELL' for t in result['trades']
                                    if t['time'] >= r.iso(53*r.HOUR)))
                self.assertLess(result['equityCurve'][-1]['exposure'], 0.00001)

    def test_peak_entry_fee_floor_denies_buy_without_latching_and_counts_hour(self):
        data = policy_data()
        # The BTC mark sets peak equity to 54.995; 94% is 51.69530.
        # At hour 51 equity is 51.69730: above the trigger, but buying would
        # cross it after the $0.005 fee. A subsequent recovery permits entry.
        set_bar(data, 50, 100, symbols=('BTCUSDT',), close=200)
        set_bar(data, 51, '134.046', symbols=('BTCUSDT',))
        set_bar(data, 52, 140, symbols=('BTCUSDT',))
        baseline = r.simulate(data, 'two', 'baseline', 50*r.HOUR, 53*r.HOUR, 0)
        for variant in ('peak_guard', 'batch_peak'):
            with self.subTest(variant=variant):
                guarded = r.simulate(data, 'two', variant, 50*r.HOUR, 53*r.HOUR, 0)
                self.assertEqual([(t['time'],t['action'],t['symbol']) for t in guarded['trades']],
                                 [(r.iso(50*r.HOUR),'BUY','BTCUSDT'),
                                  (r.iso(52*r.HOUR),'BUY','ETHUSDT')])
                s = guarded['summary']
                self.assertEqual(s['peakEntryFeeBlockedHours'], 1)
                self.assertEqual(s['blockedHours'], 1)
                self.assertEqual(s['peakBlockedHours'], 0)
                self.assertEqual(s['equityBlockedHours'], 0)
                self.assertEqual(s['ruleReasons']['PEAK_ENTRY_FLOOR'], 1)
                self.assertEqual(s['firstLossHalt'], r.iso(51*r.HOUR))
        self.assertEqual(baseline['trades'][1]['time'], r.iso(51*r.HOUR))
        self.assertEqual(baseline['summary']['peakEntryFeeBlockedHours'], 0)

    def test_prior_close_can_set_peak_but_guard_trigger_uses_decision_open(self):
        data = policy_data()
        set_bar(data, 51, 100, close=200)
        set_bar(data, 52, 160)
        result = r.simulate(data, 'two', 'peak_guard', 50*r.HOUR, 53*r.HOUR, 0)
        self.assertEqual(result['summary']['firstLossHalt'], r.iso(52*r.HOUR))
        recovered = policy_data()
        set_bar(recovered, 52, 160, close=100)
        set_bar(recovered, 53, 160)
        open_only = r.simulate(recovered, 'two', 'peak_guard', 50*r.HOUR, 54*r.HOUR, 0)
        self.assertEqual(open_only['summary']['peakBlockedHours'], 0)
        self.assertGreater(open_only['summary']['maxDrawdownPct'], 6)


class AccountingAndBenchmarkTests(unittest.TestCase):
    def test_realized_round_trip_counts_both_fees_and_reconciles(self):
        ledger = r.Ledger(('BTCUSDT',))
        ledger.fill('BUY', 'BTCUSDT', '5', D(100), 0)
        ledger.fill('SELL', 'BTCUSDT', '5', D(90), r.HOUR)
        self.assertEqual(ledger.realized, D('-0.5095'))
        self.assertEqual(ledger.fees, D('0.0095'))
        self.assertEqual(ledger.cash, D('49.4905'))
        self.assertEqual(ledger.reconcile({'BTCUSDT': D(90)}), D(0))

    def test_dust_retains_cost_merges_into_later_entry_and_reconciles(self):
        ledger = r.Ledger(('BTCUSDT',))
        ledger.fill('BUY', 'BTCUSDT', '5', D(30001), 0)
        ledger.fill('SELL', 'BTCUSDT', '5', D(32500), r.HOUR)
        ledger.fill('SELL', 'BTCUSDT', '5', D(32500), 2*r.HOUR)
        p = ledger.pos['BTCUSDT']
        self.assertGreater(p['qty'], 0)
        self.assertGreater(p['cost'], 0)
        self.assertLess(p['qty']*32500, r.MICRO)
        self.assertEqual(len(ledger.rounds), 1)
        quantity_before, basis_before = p['qty'], p['cost']
        ledger.fill('BUY', 'BTCUSDT', '5', D(30001), 3*r.HOUR)
        self.assertGreater(p['qty'], quantity_before)
        self.assertEqual(p['cost'], basis_before + 5)
        ledger.reconcile({'BTCUSDT': D(31000)})
        ledger.fill('SELL', 'BTCUSDT', '5', D(25000), 4*r.HOUR)
        self.assertEqual(len(ledger.rounds), 2)
        ledger.reconcile({'BTCUSDT': D(25000)})

    def test_passive_never_uses_strategy_stop_and_cash_stays_flat(self):
        data = policy_data()
        for i in range(55, 75):
            set_bar(data, i, 10, sma20=100, return24hPct=-90)
        for universe in r.UNIVERSES:
            with self.subTest(universe=universe):
                passive = r.simulate(data, universe, 'passive10', 50*r.HOUR, 75*r.HOUR)
                cash = r.simulate(data, universe, 'cash', 50*r.HOUR, 75*r.HOUR)
                self.assertEqual(passive['summary']['sellFills'], 0)
                self.assertEqual(passive['summary']['buyFills'], len(r.UNIVERSES[universe]))
                self.assertEqual(sum(D(t['amountUsd']) for t in passive['trades']), D(10))
                self.assertLess(passive['summary']['endEquity'], 47)
                self.assertEqual(cash['summary']['endEquity'], 50)
                self.assertEqual(cash['summary']['maxDrawdownPct'], 0)


if __name__ == '__main__':
    unittest.main()
