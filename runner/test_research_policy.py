"""Boundary tests for the frozen prospective V3 policy; no network or AI."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal as D
import unittest

import research_policy as policy

SLOT = 600000  # Divisible by six; UTC hourly slots align with UTC hours.


def fixture():
    context = {'portfolio': {'cash': '50', 'initialCapital': '50'}, 'positions': [],
               'risk': {'dailyLossLimitReached': False}, 'recentTrades': []}
    quote = {'price': '100', 'closedPrice': '100', 'sma72': '95', 'sma168': '90',
             'return24hPct': '1', 'return7dPct': '6', 'realizedVolDailyPct': '2'}
    market = {'serverTimeMs': SLOT * policy.HOUR_MS + 120000,
              'symbols': {symbol: deepcopy(quote) for symbol in policy.SYMBOLS}}
    return context, market


class ResearchPolicyTests(unittest.TestCase):
    def setUp(self):
        self.context, self.market = fixture()

    def plan(self, closing=(), state=None):
        return policy.plan(self.context, self.market, closing, state)

    def only_btc(self):
        for symbol in policy.SYMBOLS[1:]:
            self.market['symbols'][symbol]['return24hPct'] = '0'

    def test_rank_uses_risk_adjusted_momentum_and_stable_tie(self):
        self.assertEqual(self.plan()[0]['symbol'], 'BTCUSDT')
        self.market['symbols']['BTCUSDT'].update(return7dPct='9', realizedVolDailyPct='3')
        self.market['symbols']['SOLUSDT'].update(return7dPct='8', realizedVolDailyPct='2')
        candidate, _, gates, reason = self.plan()
        self.assertEqual((candidate['symbol'], reason), ('SOLUSDT', 'SLOW_TREND_ENTRY'))
        self.assertEqual(D(gates['BTCUSDT']['rankScore']), 3)
        self.assertEqual(D(gates['SOLUSDT']['rankScore']), 4)

    def test_entry_signal_uses_closed_candle_not_current_quote(self):
        self.only_btc()
        self.market['symbols']['BTCUSDT']['price'] = '1'
        self.assertEqual(self.plan()[0]['action'], 'BUY')
        for field, value in [('closedPrice', '95'), ('sma72', '90'), ('return24hPct', '0'), ('return7dPct', '0')]:
            original = self.market['symbols']['BTCUSDT'][field]
            self.market['symbols']['BTCUSDT'][field] = value
            self.assertEqual(self.plan()[0]['action'], 'HOLD')
            self.market['symbols']['BTCUSDT'][field] = original

    def test_entry_every_six_utc_hours_only(self):
        for offset in range(12):
            self.market['serverTimeMs'] = (SLOT + offset) * policy.HOUR_MS + 1
            self.assertEqual(self.plan()[0]['action'], 'BUY' if offset % 6 == 0 else 'HOLD')

    def test_volatility_scaling_and_explicit_paper_minimum(self):
        self.only_btc()
        quote = self.market['symbols']['BTCUSDT']
        for volatility, amount in [('1', '5'), ('2', '5'), ('4', '2.5'), ('10', '1')]:
            quote['realizedVolDailyPct'] = volatility
            self.assertEqual(D(self.plan()[0]['amountUsd']), D(amount))
        quote['realizedVolDailyPct'] = '10.000001'
        self.assertEqual(self.plan()[3], 'BELOW_PAPER_MINIMUM')
        quote['realizedVolDailyPct'] = '0'
        self.assertEqual(self.plan()[0]['action'], 'HOLD')

    def test_cash_capacity_and_entry_fee(self):
        self.only_btc()
        # A synthetic small-capital context exercises the affordability boundary.
        self.context['portfolio'].update(initialCapital='1', cash='1.001')
        self.assertEqual(D(self.plan()[0]['amountUsd']), 1)
        self.context['portfolio']['cash'] = '1.000999'
        self.assertEqual(self.plan()[3], 'INSUFFICIENT_CASH')
        self.context, self.market = fixture()
        self.context['positions'] = [{'symbol': s, 'quantity': '0.046875'} for s in policy.SYMBOLS[:4]]
        self.context['portfolio']['cash'] = '31.25'
        candidate = self.plan()[0]
        self.assertEqual((candidate['symbol'], D(candidate['amountUsd'])), ('XRPUSDT', D('1.25')))
        self.context['positions'][0]['quantity'] = '0.054375'
        self.assertEqual(self.plan()[3], 'MAX_EXPOSURE')

    def test_equity_floor_includes_actual_scaled_entry_fee(self):
        self.only_btc()
        self.context['portfolio']['cash'] = '47.0025'
        self.market['symbols']['BTCUSDT']['realizedVolDailyPct'] = '4'
        self.assertEqual(self.plan()[3], 'EQUITY_LOSS_LIMIT')
        self.context['portfolio']['cash'] = '47.002501'
        self.assertEqual(D(self.plan()[0]['amountUsd']), D('2.5'))

    def test_no_averaging_and_cooldown_from_completed_sell(self):
        self.only_btc()
        self.context['positions'] = [{'symbol': 'BTCUSDT', 'quantity': '0.02'}]
        self.assertEqual(self.plan()[0]['action'], 'HOLD')
        self.context['positions'] = []
        sold = {'side': 'SELL', 'symbol': 'BTCUSDT',
                'createdAt': datetime.fromtimestamp((SLOT - 23) * 3600, timezone.utc).isoformat()}
        self.context['recentTrades'] = [sold]
        self.assertEqual(self.plan()[3], 'REENTRY_COOLDOWN')
        sold['createdAt'] = datetime.fromtimestamp((SLOT - 24) * 3600, timezone.utc).isoformat()
        self.assertEqual(self.plan()[0]['action'], 'BUY')

    def test_persisted_cooldown_survives_truncated_trade_history_without_mutation(self):
        self.only_btc()
        state = {'lastExitSlot': {'BTCUSDT': SLOT - 1}}
        snapshot = deepcopy((self.context, self.market, state))
        result = self.plan(state=state)
        self.assertEqual(result[3], 'REENTRY_COOLDOWN')
        self.assertEqual(result[2]['BTCUSDT']['cooldownRemainingHours'], 23)
        self.assertEqual((self.context, self.market, state), snapshot)
        state['lastExitSlot']['BTCUSDT'] = SLOT + 1
        with self.assertRaisesRegex(ValueError, 'exit slot'):
            self.plan(state=state)

    def test_risk_sell_is_hourly_and_cooldown_never_blocks_it(self):
        self.market['serverTimeMs'] += policy.HOUR_MS
        self.context['positions'] = [{'symbol': 'ETHUSDT', 'quantity': '0.08'}]
        self.context['risk']['dailyLossLimitReached'] = True
        candidate, closing, _, reason = self.plan(state={'lastExitSlot': {'ETHUSDT': SLOT}})
        self.assertEqual((candidate['action'], candidate['symbol'], D(candidate['amountUsd'])), ('SELL', 'ETHUSDT', D(5)))
        self.assertEqual((closing, reason), (['ETHUSDT'], 'RISK_EXIT'))
        self.context['risk']['dailyLossLimitReached'] = False
        self.context['portfolio']['cash'] = '38'
        self.assertEqual(self.plan()[3], 'RISK_EXIT')

    def test_broad_exit_and_sticky_liquidation(self):
        self.context['positions'] = [{'symbol': 'SOLUSDT', 'quantity': '0.075'}]
        self.market['symbols']['SOLUSDT']['closedPrice'] = '94'
        candidate, closing, _, _ = self.plan()
        self.assertEqual((candidate['action'], D(candidate['amountUsd'])), ('SELL', D(5)))
        self.market['symbols']['SOLUSDT']['closedPrice'] = '100'
        self.context['positions'][0]['quantity'] = '0.025000001'
        self.assertEqual(D(self.plan(closing)[0]['amountUsd']), D('2.500000'))
        self.assertEqual(self.plan()[0]['action'], 'BUY')  # New entries only without saved closing state.
        self.market['symbols']['SOLUSDT']['return7dPct'] = '0'
        self.assertEqual(self.plan()[0]['action'], 'SELL')

    def test_nonfinite_features_and_foreign_positions_fail_closed(self):
        self.market['symbols']['BTCUSDT']['realizedVolDailyPct'] = 'NaN'
        with self.assertRaises(ValueError):
            self.plan()
        self.context, self.market = fixture()
        self.context['positions'] = [{'symbol': 'DOGEUSDT', 'quantity': '1'}]
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.plan()


if __name__ == '__main__':
    unittest.main()
