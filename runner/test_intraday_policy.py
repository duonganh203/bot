"""Pure V4 boundary tests; signals, exits, sizing and diagnostics are separate."""
from copy import deepcopy
from decimal import Decimal as D
import unittest

import intraday_policy as policy

BAR = 600000  # Divisible by 24, the six-hour slow entry schedule.


def fixture():
    context = {'portfolio': {'cash': '50', 'initialCapital': '50'}, 'positions': [],
               'risk': {'dailyLossLimitReached': False}}
    feature = {'closedPrice15m': '101', 'previousClose15m': '99', 'previousLow15m': '98',
               'previousEma20_15m': '99', 'ema20_15m': '100', 'atr14_15m': '1',
               'closedPrice4h': '100', 'sma20_4h': '95', 'sma50_4h': '90',
               'closedPrice': '100', 'sma72': '95', 'sma168': '90',
               'return24hPct': '1', 'return7dPct': '6', 'realizedVolDailyPct': '2'}
    features = {'bar': BAR, 'symbols': {s: deepcopy(feature) for s in policy.SYMBOLS}}
    quote = {'bid': '100', 'ask': '100', 'bidQty': '100', 'askQty': '100', 'receivedAt': BAR * 900 + 5}
    book = {'serverTimeMs': BAR * policy.BAR_MS + 5000, 'startedAt': BAR * 900 + 5,
            'completedAt': BAR * 900 + 6, 'symbols': {s: deepcopy(quote) for s in policy.SYMBOLS}}
    state = {'positions': {}, 'lastExitTime': {}}
    return context, features, book, state


class IntradayPolicyTests(unittest.TestCase):
    def setUp(self):
        self.context, self.features, self.book, self.state = fixture()

    @property
    def now(self):
        return self.book['serverTimeMs'] / 1000

    def plan(self, mode='pullback', closing=(), allow_entry=True, evaluation_time=None):
        return policy.plan(self.context, self.features, self.book, mode, self.state, closing, allow_entry, evaluation_time)

    def advance(self, seconds):
        self.book['serverTimeMs'] += seconds * 1000
        self.features['bar'] = self.book['serverTimeMs'] // policy.BAR_MS

    def position(self, symbol='BTCUSDT', quantity='0.05', age=0, price='100', stop='98'):
        self.context['positions'].append({'symbol': symbol, 'quantity': quantity})
        self.state['positions'][symbol] = {'entryTime': self.now - age, 'entryPrice': price, 'stopPrice': stop}

    def only_btc(self):
        for symbol in policy.SYMBOLS[1:]:
            self.features['symbols'][symbol].update(closedPrice4h='94', return24hPct='0')

    def test_predeclared_rank_and_tie_break_are_shared(self):
        for mode in policy.POLICY['modes']:
            self.assertEqual(self.plan(mode)[0]['symbol'], 'BTCUSDT')
        self.features['symbols']['BTCUSDT'].update(return7dPct='9', realizedVolDailyPct='3')
        self.features['symbols']['SOLUSDT'].update(return7dPct='8', realizedVolDailyPct='2')
        for mode in policy.POLICY['modes']:
            candidate, _, gates, _ = self.plan(mode)
            self.assertEqual(candidate['symbol'], 'SOLUSDT')
            self.assertEqual(D(gates['BTCUSDT']['rankScore']), 3)
            self.assertEqual(D(gates['SOLUSDT']['rankScore']), 4)

    def test_pullback_removes_both_return_sign_requirements(self):
        for f in self.features['symbols'].values():
            f.update(return24hPct='-1', return7dPct='-2')
        self.assertEqual(self.plan()[3], 'PULLBACK_ENTRY')
        self.assertEqual(self.plan('slow')[0]['action'], 'HOLD')
        self.assertNotIn('positive24hReturn', self.plan()[2]['BTCUSDT']['entryChecks'])

    def test_entry_is_based_on_closed_features_not_current_book_price(self):
        self.only_btc()
        for mode in policy.POLICY['modes']:
            for live_price in ('1', '10000'):
                self.book['symbols']['BTCUSDT'].update(bid=live_price, ask=live_price)
                self.assertEqual(self.plan(mode)[0]['action'], 'BUY')
        self.features['symbols']['BTCUSDT']['closedPrice15m'] = '99'
        self.assertEqual(self.plan()[0]['action'], 'HOLD')
        self.assertEqual(self.plan('slow')[0]['action'], 'BUY')

    def test_every_pullback_feature_gate_is_independently_visible(self):
        self.only_btc()
        cases = [('closedPrice4h', '95', 'closed4hAboveSma20'),
                 ('sma20_4h', '90', 'sma20AboveSma50_4h'),
                 ('previousLow15m', '99.000001', 'previous15mTouchedEma20'),
                 ('closedPrice15m', '100', 'closed15mAboveEma20'),
                 ('previousClose15m', '101', 'closed15mAbovePreviousClose')]
        for field, value, gate in cases:
            original = self.features['symbols']['BTCUSDT'][field]
            self.features['symbols']['BTCUSDT'][field] = value
            result = self.plan()
            self.assertEqual(result[0]['action'], 'HOLD')
            self.assertIn(gate, result[2]['BTCUSDT']['failedGates'])
            self.features['symbols']['BTCUSDT'][field] = original

    def test_slow_schedule_and_failed_trend_do_not_hide_each_other(self):
        self.only_btc()
        self.features['symbols']['BTCUSDT']['return24hPct'] = '-1'
        self.advance(900)
        _, _, gates, reason = self.plan('slow')
        self.assertEqual(reason, 'ENTRY_SCHEDULE')
        self.assertIn('entrySchedule', gates['BTCUSDT']['failedGates'])
        self.assertIn('positive24hReturn', gates['BTCUSDT']['failedGates'])
        self.features['symbols']['BTCUSDT']['return24hPct'] = '1'
        for offset in range(24):
            self.features['bar'] = BAR + offset
            self.book['serverTimeMs'] = (BAR + offset) * policy.BAR_MS + 5000
            self.assertEqual(self.plan('slow')[0]['action'], 'BUY' if offset == 0 else 'HOLD')
            self.assertEqual(self.plan()[0]['action'], 'BUY')

    def test_runner_entry_gate_disables_entry_without_disabling_exits(self):
        self.assertEqual(self.plan(allow_entry=False)[3], 'ENTRY_NOT_ALLOWED')
        self.position()
        self.book['symbols']['BTCUSDT']['bid'] = '98'
        result = self.plan(allow_entry=False)
        self.assertEqual(result[0]['action'], 'SELL')
        self.assertIn('entryAllowed', result[2]['BTCUSDT']['failedGates'])

    def test_both_modes_share_fixed_stop_at_bid_boundary(self):
        self.position()
        self.advance(60)  # No new closed 15m candle is necessary for a stop.
        for mode in policy.POLICY['modes']:
            self.book['symbols']['BTCUSDT']['bid'] = '98.000001'
            self.assertEqual(self.plan(mode, allow_entry=False)[0]['action'], 'HOLD')
            self.book['symbols']['BTCUSDT']['bid'] = '98'
            candidate, closing, gates, reason = self.plan(mode, allow_entry=False)
            self.assertEqual((candidate['action'], D(candidate['amountUsd'])), ('SELL', D('4.9')))
            self.assertEqual(closing, ['BTCUSDT'])
            self.assertEqual(reason, 'POSITION_EXIT')
            self.assertIn('fixedStop', gates['BTCUSDT']['exitReasons'])

    def test_maximum_hold_is_same_twelve_hours_for_both_modes(self):
        self.position(age=43199)
        for mode in policy.POLICY['modes']:
            self.assertEqual(self.plan(mode, allow_entry=False)[0]['action'], 'HOLD')
        self.advance(1)
        for mode in policy.POLICY['modes']:
            result = self.plan(mode, allow_entry=False)
            self.assertEqual(result[0]['action'], 'SELL')
            self.assertIn('maximumHold', result[2]['BTCUSDT']['exitReasons'])

    def test_common_closed_four_hour_exit_is_independent_of_entry_clock(self):
        self.position('ETHUSDT', quantity='0.08')
        self.advance(900)
        self.features['symbols']['ETHUSDT']['closedPrice4h'] = '94'
        for mode in policy.POLICY['modes']:
            candidate, closing, gates, _ = self.plan(mode)
            self.assertEqual((candidate['action'], candidate['symbol'], D(candidate['amountUsd'])), ('SELL', 'ETHUSDT', D(5)))
            self.assertEqual(closing, ['ETHUSDT'])
            self.assertIn('closed4hBelowSma20', gates['ETHUSDT']['exitReasons'])

    def test_sticky_exit_remains_after_rebound_and_caps_partial_order(self):
        self.position('SOLUSDT', quantity='0.08')
        self.assertEqual(D(self.plan(closing=['SOLUSDT'])[0]['amountUsd']), 5)
        self.context['positions'][0]['quantity'] = '0.025000001'
        result = self.plan(closing=['SOLUSDT'])
        self.assertEqual(D(result[0]['amountUsd']), D('2.500000'))
        self.assertIn('stickyClosing', result[2]['SOLUSDT']['exitReasons'])

    def test_risk_exits_override_daily_floor_cooldown_and_schedule(self):
        self.position('ETHUSDT', quantity='0.08')
        self.advance(900)
        self.state['lastExitTime']['ETHUSDT'] = self.now - 60
        self.context['risk']['dailyLossLimitReached'] = True
        for mode in policy.POLICY['modes']:
            self.assertEqual(self.plan(mode)[3], 'RISK_EXIT')
        self.context['risk']['dailyLossLimitReached'] = False
        self.context['portfolio']['cash'] = '39'
        for mode in policy.POLICY['modes']:
            self.assertEqual(self.plan(mode)[3], 'RISK_EXIT')

    def test_twenty_four_hour_cooldown_uses_elapsed_seconds(self):
        self.only_btc()
        self.state['lastExitTime']['BTCUSDT'] = self.now - 86400 + 1
        for mode in policy.POLICY['modes']:
            result = self.plan(mode)
            self.assertEqual(result[3], 'REENTRY_COOLDOWN')
            self.assertEqual(D(result[2]['BTCUSDT']['cooldownRemainingSeconds']), 1)
        self.state['lastExitTime']['BTCUSDT'] -= 1
        for mode in policy.POLICY['modes']:
            self.assertEqual(self.plan(mode)[0]['action'], 'BUY')

    def test_no_adding_and_sub_microdollar_dust_is_explicit(self):
        self.only_btc()
        self.position()
        self.assertEqual(self.plan()[0]['action'], 'HOLD')
        self.context['positions'][0]['quantity'] = '0.000000009'
        self.state['positions'] = {}
        candidate, closing, _, _ = self.plan(closing=['BTCUSDT'])
        self.assertEqual(candidate['action'], 'BUY')
        self.assertEqual(closing, [])

    def test_volatility_sizing_is_identical_and_minimum_is_paper_only(self):
        self.only_btc()
        for vol, expected in [('1', '5'), ('2', '5'), ('4', '2.5'), ('10', '1')]:
            self.features['symbols']['BTCUSDT']['realizedVolDailyPct'] = vol
            for mode in policy.POLICY['modes']:
                self.assertEqual(D(self.plan(mode)[0]['amountUsd']), D(expected))
        self.features['symbols']['BTCUSDT']['realizedVolDailyPct'] = '10.000001'
        self.assertEqual(self.plan()[3], 'BELOW_PAPER_MINIMUM')
        self.features['symbols']['BTCUSDT']['realizedVolDailyPct'] = '0'
        self.assertEqual(self.plan()[0]['action'], 'HOLD')

    def test_cash_fee_and_exposure_caps(self):
        self.only_btc()
        self.context['portfolio'].update(initialCapital='1', cash='1.001')
        self.assertEqual(D(self.plan()[0]['amountUsd']), 1)
        self.context['portfolio']['cash'] = '1.000999'
        self.assertEqual(self.plan()[3], 'INSUFFICIENT_CASH')
        self.setUp()
        for symbol in policy.SYMBOLS[:4]:
            self.position(symbol, quantity='0.046875')
        self.context['portfolio']['cash'] = '31.25'
        self.assertEqual(D(self.plan()[0]['amountUsd']), D('1.25'))
        self.context['positions'][0]['quantity'] = '0.054375'
        self.assertEqual(self.plan()[3], 'MAX_EXPOSURE')

    def test_equity_entry_gate_accounts_for_fee_and_decision_spread(self):
        self.only_btc()
        self.features['symbols']['BTCUSDT']['realizedVolDailyPct'] = '4'
        self.context['portfolio']['cash'] = '47.0025'
        self.assertEqual(self.plan()[3], 'EQUITY_LOSS_LIMIT')
        self.context['portfolio']['cash'] = '47.002501'
        self.assertEqual(self.plan()[0]['action'], 'BUY')
        self.book['symbols']['BTCUSDT']['ask'] = '100.1'
        self.assertEqual(self.plan()[3], 'EQUITY_LOSS_LIMIT')

    def test_missing_entry_metadata_or_future_state_fails_closed(self):
        self.context['positions'] = [{'symbol': 'BTCUSDT', 'quantity': '0.05'}]
        with self.assertRaisesRegex(ValueError, 'confirmed entry metadata'):
            self.plan()
        self.context['positions'] = []
        self.state['lastExitTime']['BTCUSDT'] = self.now + 1
        with self.assertRaisesRegex(ValueError, 'exit time'):
            self.plan()
        self.state['lastExitTime'] = {}
        self.position(age=-1)
        with self.assertRaisesRegex(ValueError, 'entry time'):
            self.plan()

    def test_stale_feature_bar_and_invalid_numbers_fail_closed(self):
        self.features['bar'] -= 1
        with self.assertRaisesRegex(ValueError, 'Feature bar'):
            self.plan()
        self.features['bar'] += 1
        for invalid in ('NaN', 'Infinity', '-1', True, 'bad'):
            self.features['symbols']['BTCUSDT']['atr14_15m'] = invalid
            with self.assertRaises(ValueError):
                self.plan()

    def test_actual_evaluation_time_accepts_confirmed_exit_after_book(self):
        # A first SELL can finish after the shared decision book; a following
        # reducing SELL in the same cycle must not fail the cooldown audit.
        self.state['lastExitTime']['BTCUSDT'] = self.now + 10
        self.position('ETHUSDT', quantity='0.08')
        result = self.plan(closing=['ETHUSDT'], allow_entry=False, evaluation_time=self.now + 11)
        self.assertEqual((result[0]['action'], result[0]['symbol']), ('SELL', 'ETHUSDT'))
        self.assertEqual(D(result[2]['BTCUSDT']['cooldownRemainingSeconds']), 86399)

    def test_actual_evaluation_clock_uses_elapsed_holding_and_has_bound(self):
        self.position(age=43195)
        self.assertEqual(self.plan(allow_entry=False)[0]['action'], 'HOLD')
        result = self.plan(allow_entry=False, evaluation_time=self.now + 5)
        self.assertEqual(result[0]['action'], 'SELL')
        self.assertIn('maximumHold', result[2]['BTCUSDT']['exitReasons'])
        for timestamp in (self.now + 120.001, self.now - 120.001, float('nan'), True):
            with self.assertRaises(ValueError):
                self.plan(evaluation_time=timestamp)

    def test_actual_evaluation_clock_does_not_advance_feature_bar(self):
        self.book['serverTimeMs'] = (BAR + 1) * policy.BAR_MS - 1000
        self.features['bar'] = BAR
        # The decision remains in the slow six-hour entry bar even if its
        # bounded execution/evaluation occurs just into the next bar.
        self.assertEqual(self.plan('slow', evaluation_time=self.now + 2)[0]['action'], 'BUY')

    def test_crossed_quote_and_foreign_position_fail_closed(self):
        self.book['symbols']['BTCUSDT']['bid'] = '101'
        with self.assertRaisesRegex(ValueError, 'bid/ask'):
            self.plan()
        self.book['symbols']['BTCUSDT']['bid'] = '100'
        self.context['positions'] = [{'symbol': 'DOGEUSDT', 'quantity': '1'}]
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.plan()

    def test_policy_does_not_mutate_evidence_or_saved_state(self):
        self.position('ETHUSDT')
        self.state['lastExitTime']['BTCUSDT'] = self.now - 1
        before = deepcopy((self.context, self.features, self.book, self.state))
        result = self.plan(closing=['ETHUSDT'])
        self.assertEqual(result[0]['action'], 'SELL')
        self.assertEqual((self.context, self.features, self.book, self.state), before)


if __name__ == '__main__':
    unittest.main()
