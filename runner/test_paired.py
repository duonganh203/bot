"""V2 policy, independent event consumers, fail-closed entry and recovery checks."""
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import paired
import policy
import market_snapshot as feed
from test_shadow import fixture, NOW, SLOT


class PairedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.context, source, _, _ = fixture()
        self.market = source['market']
        self.context['risk'].update(policy='reduce-only-v2', maxEquityLossUsd=3, reducingSellsAllowed=True)
        self.exp = {'schemaVersion': 2, 'version': paired.version(), 'startSlot': SLOT,
                    'backends': {'ai': 'http://localhost:3000', 'control': 'http://localhost:3001'}}
        paired.core.save_json(self.root / 'experiment.json', self.exp)
        (self.root / 'market').mkdir()
        self.event = {'schemaVersion': 2, 'slot': SLOT, 'marketId': feed.digest(self.market), 'market': self.market}
        paired.core.save_json(self.root / 'market' / (str(SLOT) + '.json'), self.event)

    def tearDown(self):
        self.tmp.cleanup()

    def execute(self, mode, dry=True):
        with patch.object(paired.time, 'time', return_value=NOW), patch.object(paired.core, 'fetch_context', return_value=self.context):
            return paired.execute(self.root, mode, dry)

    def test_control_never_reads_or_calls_ai(self):
        with patch.object(paired.entry_filter, 'vote', side_effect=AssertionError('AI called')):
            result = self.execute('control')
        self.assertEqual(result['payload']['action'], 'BUY')
        self.assertFalse((self.root / 'ai').exists())
        self.assertFalse((self.root / 'control' / 'state.json').exists())

    def test_ai_cannot_resize_or_change_entry_and_errors_are_distinct_from_veto(self):
        for approved in (True, False):
            with patch.object(paired.entry_filter, 'vote', return_value={'approve': approved, 'rationale': 'test'}):
                ai = self.execute('ai')
            control = self.execute('control')
            if approved:
                for field in ('action', 'symbol', 'amountUsd', 'price'):
                    self.assertEqual(ai['payload'][field], control['payload'][field])
            else:
                self.assertEqual(ai['payload']['action'], 'HOLD')
        with patch.object(paired.entry_filter, 'vote', side_effect=RuntimeError('timeout')):
            failed = self.execute('ai')
        self.assertEqual(failed['payload']['action'], 'HOLD')
        evidence = paired.core.read_json(Path(failed['runDir']) / 'evidence.json')
        self.assertEqual(evidence['aiVote']['status'], 'error')
        self.assertEqual(self.execute('control')['payload']['action'], 'BUY')

    def test_loss_gates_force_sell_even_when_ai_unavailable_or_trend_is_up(self):
        self.context['positions'] = [{'symbol': 'BTCUSDT', 'quantity': 0.025}]
        self.context['portfolio']['cash'] = 40
        for daily in (True, False):
            self.context['risk']['dailyLossLimitReached'] = daily
            with patch.object(paired.entry_filter, 'vote', side_effect=AssertionError('Exit must not call AI')):
                result = self.execute('ai')
            self.assertEqual(result['payload']['action'], 'SELL')
            self.assertEqual(result['payload']['amountUsd'], '5')

    def test_no_adding_and_sticky_exit_honors_dust_and_six_decimals(self):
        self.context['portfolio']['cash'] = 44.995
        self.context['positions'] = [{'symbol': 'BTCUSDT', 'quantity': 0.03}]
        candidate, closing, _, _ = policy.plan(self.context, self.market, ['BTCUSDT'])
        self.assertEqual((candidate['action'], candidate['amountUsd'], closing), ('SELL', '5.000000', ['BTCUSDT']))
        self.context['positions'][0]['quantity'] = 0.000001234567
        candidate = policy.plan(self.context, self.market, ['BTCUSDT'])[0]
        self.assertEqual(candidate['amountUsd'], '0.000246')
        self.assertLessEqual(D(candidate['amountUsd']), D('0.000001234567') * 200)
        self.context['positions'][0]['quantity'] = 0.025
        self.assertEqual(policy.plan(self.context, self.market)[0]['symbol'], 'ETHUSDT')

    def test_entry_fee_is_part_of_equity_floor(self):
        self.context['portfolio']['cash'] = '47.005'
        self.assertEqual(policy.plan(self.context, self.market)[3], 'EQUITY_LOSS_LIMIT')
        self.context['portfolio']['cash'] = '47.005001'
        self.assertEqual(policy.plan(self.context, self.market)[0]['action'], 'BUY')

    def test_market_missing_stale_tampered_and_code_change_block(self):
        path = self.root / 'market' / (str(SLOT) + '.json')
        event = deepcopy(self.event)
        event['market']['symbols']['BTCUSDT']['price'] = '201'
        paired.core.save_json(path, event)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.execute('control')
        path.unlink()
        self.assertEqual(self.execute('control')['status'], 'waiting')
        paired.core.save_json(path, self.event)
        with patch.object(paired.time, 'time', return_value=NOW + 200):
            with self.assertRaisesRegex(ValueError, 'expired'):
                paired.execute(self.root, 'control')
        self.exp['version'] = 'changed'
        paired.core.save_json(self.root / 'experiment.json', self.exp)
        with self.assertRaisesRegex(ValueError, 'code changed'):
            self.execute('control')

    def test_market_publisher_is_immutable_and_independent(self):
        with patch.object(feed.time, 'time', return_value=NOW), patch.object(feed.core, 'collect_market', side_effect=AssertionError('Recollected')):
            self.assertEqual(feed.publish(self.root / 'market')['status'], 'skipped')

    def test_pending_request_is_replayed_identically_before_new_decision(self):
        saved = []
        def lost(directory, state):
            saved.append(deepcopy(state))
            raise OSError('unknown response')
        with patch.object(paired.core, 'deliver', side_effect=lost):
            with self.assertRaises(OSError):
                self.execute('control', False)
        original = paired.core.read_json(self.root / 'control' / 'state.json')
        with patch.object(paired.core, 'fetch_context', side_effect=AssertionError('new context')):
            with patch.object(paired.core, 'deliver', return_value={'recovered': True}) as deliver:
                self.assertEqual(paired.execute(self.root, 'control'), {'recovered': True})
        self.assertEqual(deliver.call_args.args[1], original)
        self.assertEqual(saved[0], original)

    def test_completed_slot_deduplicates_and_repairs_missing_audit_copy(self):
        run_dir = self.root / 'control' / 'runs' / 'test'
        run_dir.mkdir(parents=True)
        state = {'complete': True, 'backend': self.exp['backends']['control'], 'slot': SLOT,
                 'runDir': str(run_dir), 'result': {'response': {'status': 'held', 'decisionId': str(uuid.uuid4())}}}
        paired.core.save_json(self.root / 'control' / 'state.json', state)
        self.assertEqual(self.execute('control', False)['status'], 'skipped')
        self.assertEqual(paired.core.read_json(run_dir / 'result.json'), state['result'])


if __name__ == '__main__':
    unittest.main()
