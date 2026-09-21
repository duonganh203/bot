from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import intraday_runner as runner


def event(now=1_800_000_000):
    book = {'startedAt': now - 4, 'completedAt': now - 3, 'serverTimeMs': int((now - 4) * 1000),
            'symbols': {s: {'bid': '100', 'ask': '100.1', 'bidQty': '1', 'askQty': '1',
                            'receivedAt': now - 3} for s in runner.policy.SYMBOLS}}
    execution = deepcopy(book)
    execution.update(startedAt=now - 1, completedAt=now)
    for q in execution['symbols'].values():
        q['receivedAt'] = now - 1
    return {'decision': book, 'execution': execution,
            'features': {'symbols': {s: {'atr14_15m': '1'} for s in runner.policy.SYMBOLS}}}


def context(cash='50', qty=None):
    return {'portfolio': {'cash': cash, 'initialCapital': '50', 'version': 0},
            'positions': [] if qty is None else [{'symbol': 'BTCUSDT', 'quantity': qty}],
            'risk': {'dailyLossLimitReached': False}}


class ExecutionTests(unittest.TestCase):
    def test_buy_uses_later_ask_and_adverse_rounding(self):
        e = event()
        e['execution']['symbols']['BTCUSDT']['ask'] = '100.123456'
        result, blocked = runner.execution_order({'action': 'BUY', 'symbol': 'BTCUSDT', 'amountUsd': '5'},
                                                context(), e, {'liquidityUsed': {}}, 1_800_000_000)
        self.assertIsNone(blocked)
        self.assertEqual(result['price'], '100.143481')
        self.assertEqual(result['execution']['decisionToExecutionQuoteMs'], 2000)

    def test_sell_sizes_at_actual_fill_and_never_oversells(self):
        result, _ = runner.execution_order({'action': 'SELL', 'symbol': 'BTCUSDT', 'amountUsd': '5'},
                                          context('45', '0.05'), event(), {'liquidityUsed': {}}, 1_800_000_000)
        self.assertEqual(result['price'], '99.980000')
        self.assertEqual(result['amountUsd'], '4.999000')
        self.assertLessEqual(D(result['amountUsd']) / D(result['price']), D('.05'))

    def test_displayed_liquidity_is_consumed_across_chunks(self):
        e = event()
        e['execution']['symbols']['BTCUSDT']['bidQty'] = '.06'
        result, _ = runner.execution_order({'action': 'SELL', 'symbol': 'BTCUSDT', 'amountUsd': '5'},
                                          context('40', '.1'), e, {'liquidityUsed': {'BTCUSDT:SELL': '.05'}}, 1_800_000_000)
        self.assertEqual(result['amountUsd'], '0.999800')

    def test_later_spread_can_cancel_but_not_select_new_buy(self):
        e = event()
        e['execution']['symbols']['BTCUSDT']['ask'] = '200'
        result, reason = runner.execution_order({'action': 'BUY', 'symbol': 'BTCUSDT', 'amountUsd': '5'},
                                               context('49'), e, {'liquidityUsed': {}}, 1_800_000_000)
        self.assertIsNone(result)
        self.assertEqual(reason, 'EXECUTION_RISK_RECHECK')

    def test_expired_book_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'expired'):
            runner.execution_order({'action': 'BUY', 'symbol': 'BTCUSDT', 'amountUsd': '5'},
                                   context(), event(), {'liquidityUsed': {}}, 1_800_000_100)

    def test_durable_committed_action_applies_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            action_dir = directory / 'requests' / 'one'
            action_dir.mkdir(parents=True)
            state = runner.new_state('http://127.0.0.1:3007')
            state['cycle'] = {'actions': [], 'entryEligible': True, 'liquidityUsed': {}}
            state['pending'] = {'directory': str(action_dir), 'action': {'action': 'BUY', 'symbol': 'BTCUSDT',
                                'execution': {'executionAsk': '100', 'executionBid': '99', 'executionReferencePrice': '100',
                                              'decisionQuoteReceivedAt': 123}},
                                'budgetKey': 'BTCUSDT:BUY', 'entryMetadata': {'entryTime': 123, 'stopPrice': '98'}}
            runner.core.save_json(action_dir / 'state.json', {
                'backend': state['backend'], 'complete': True, 'result': {'replayed': True, 'response': {
                    'status': 'executed', 'decisionId': 'saved', 'trade': {'quantity': '.05', 'price': '100.02',
                                                                       'createdAt': '2026-09-21T00:00:00Z'}}}})
            with patch.object(runner.core, 'deliver', side_effect=AssertionError('Must not replay committed state')):
                runner.recover_pending(directory, state)
                runner.recover_pending(directory, state)
            self.assertEqual(state['expectedVersion'], 1)
            self.assertEqual(len(state['cycle']['actions']), 1)
            self.assertEqual(state['policyState']['positions']['BTCUSDT']['stopPrice'], '98')
            self.assertFalse(state['cycle']['entryEligible'])


if __name__ == '__main__':
    unittest.main()
