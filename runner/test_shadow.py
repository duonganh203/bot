"""Control-policy and integration tests: no external HTTP, AI calls, or real trades."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import shadow

NOW = 497055 * 3600 + 210
SLOT = NOW // 3600


def fixture():
    market = {'startedAt': NOW - 90, 'serverTimeMs': (NOW - 90) * 1000, 'symbols': {}}
    for symbol in shadow.core.SYMBOLS:
        rows = [[(SLOT - 100 + i) * 3600000, str(100 + i), str(101 + i), str(99 + i),
                 str(100 + i), '10'] for i in range(100)]
        closes = [D(r[4]) for r in rows]
        market['symbols'][symbol] = {'price': '200', 'closedHourlyCandles': rows,
            'sma20': str(sum(closes[-20:]) / 20), 'sma50': str(sum(closes[-50:]) / 50),
            'return24hPct': str((closes[-1] / closes[-25] - 1) * 100)}
    context = {'contextId': str(uuid.uuid4()), 'asOf': datetime.fromtimestamp(NOW - 80, timezone.utc).isoformat(),
        'portfolio': {'initialCapital': 50, 'version': 0, 'cash': 50, 'equity': 50}, 'positions': [],
        'risk': {'maxOrderUsd': 5, 'maxExposureUsd': 20, 'feeRate': 0.001,
                 'maxDailyLossUsd': 3, 'dailyLossLimitReached': False}}
    source = {'market': market, 'context': deepcopy(context)}
    source['context']['contextId'] = str(uuid.uuid4())
    body = {'action': 'HOLD', 'symbol': None, 'rationale': 'Wait.', 'strategyId': 'codex-trend',
            'strategyVersion': 'v1-ai', 'contextId': source['context']['contextId']}
    state = {'complete': True, 'backend': 'http://localhost:3000', 'slot': SLOT, 'slotSeconds': 3600,
             'runDir': '/source/runs/test', 'body': json.dumps(body), 'result': {'httpStatus': 200,
             'response': {'status': 'held', 'decisionId': str(uuid.uuid4())}}}
    return context, source, body, state


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name) / 'control'
        self.source_dir = Path(self.tmp.name) / 'ai'
        self.directory.mkdir()
        self.source_dir.mkdir()
        self.context, self.source, self.body, self.ai_state = fixture()
        self.baseline, self.backend = 'http://localhost:3000', 'http://localhost:3001'
        self.experiment = {'backend': self.backend, 'baseline': self.baseline, 'startSlot': SLOT,
            'strategyVersion': shadow.version(), 'aiStrategyVersion': 'v1-ai',
            'sourceDir': str(self.source_dir.resolve())}
        shadow.core.save_json(self.directory / 'experiment.json', self.experiment)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, closing=()):
        return shadow.plan(self.context, self.source['market'], closing)

    def test_eligible_entry_is_mandatory_and_ties_are_stable(self):
        candidate, _, gates, _ = self.plan()
        self.assertTrue(all(g['entry'] for g in gates.values()))
        self.assertEqual((candidate['action'], candidate['symbol'], candidate['amountUsd']), ('BUY', 'BTCUSDT', '5'))
        self.context['positions'] = [{'symbol': 'BTCUSDT', 'quantity': 0.025}]
        self.assertEqual(self.plan()[0]['symbol'], 'ETHUSDT')
        self.context['positions'].append({'symbol': 'ETHUSDT', 'quantity': 0.025})
        self.assertEqual(self.plan()[0]['action'], 'HOLD')

    def test_strict_boundaries_and_negative_return_do_not_force_trades(self):
        for q in self.source['market']['symbols'].values():
            q['price'] = q['sma20']
        self.assertEqual(self.plan()[0]['action'], 'HOLD')
        for q in self.source['market']['symbols'].values():
            q.update(price='200', return24hPct='0')
        self.assertEqual(self.plan()[0]['action'], 'HOLD')

    def test_cash_fee_exposure_and_daily_loss(self):
        self.context['portfolio']['cash'] = 5
        self.assertEqual(self.plan()[3], 'INSUFFICIENT_CASH')
        self.context['portfolio']['cash'] = 50
        self.context['positions'] = [{'symbol': 'BTCUSDT', 'quantity': 0.08}]
        self.assertEqual(self.plan()[3], 'MAX_EXPOSURE')
        self.context['risk']['dailyLossLimitReached'] = True
        self.assertEqual(self.plan()[3], 'DAILY_LOSS_LIMIT')

    def test_exit_priority_and_sticky_partial_exit(self):
        self.context['positions'] = [{'symbol': 'ETHUSDT', 'quantity': 0.08}]
        self.source['market']['symbols']['ETHUSDT'].update(price='100', sma20='110', return24hPct='-1')
        candidate, closing, _, _ = self.plan()
        self.assertEqual((candidate['action'], candidate['symbol'], candidate['amountUsd']), ('SELL', 'ETHUSDT', '5.000000'))
        self.assertEqual(closing, ['ETHUSDT'])
        self.source['market']['symbols']['ETHUSDT'].update(price='200', return24hPct='1')
        self.assertEqual(self.plan(closing)[0]['action'], 'SELL')
        self.context['risk']['dailyLossLimitReached'] = True
        self.assertEqual(self.plan(closing)[0]['action'], 'HOLD')
        self.assertEqual(self.plan(closing)[1], ['ETHUSDT'])

    def test_sell_rounds_down_and_dust_does_not_freeze_entry(self):
        self.context['positions'] = [{'symbol': 'BTCUSDT', 'quantity': '0.00123456789'}]
        candidate = self.plan(['BTCUSDT'])[0]
        self.assertEqual(candidate['amountUsd'], '0.246913')
        self.assertLessEqual(D(candidate['amountUsd']), D('0.00123456789') * 200)
        self.context['positions'][0]['quantity'] = '0.000000000001'
        self.assertEqual(self.plan(['BTCUSDT'])[0]['action'], 'BUY')

    def test_future_candle_and_changed_indicator_rejected(self):
        market = self.source['market']
        self.assertEqual(shadow.validate_market(market), market)
        wrong = deepcopy(market)
        wrong['symbols']['BTCUSDT']['sma20'] = '1'
        with self.assertRaisesRegex(ValueError, 'indicator'):
            shadow.validate_market(wrong)
        wrong = deepcopy(market)
        wrong['symbols']['BTCUSDT']['closedHourlyCandles'][-1][0] += shadow.core.HOUR_MS
        with self.assertRaises(ValueError):
            shadow.validate_market(wrong)

    def test_alias_to_primary_backend_is_rejected(self):
        with patch.object(shadow.core, 'fetch_context', return_value=self.context), \
             patch.object(shadow.core, 'http', return_value=(200, {}, {})):
            with self.assertRaisesRegex(ValueError, 'isolation'):
                shadow.separate_context(self.backend, self.baseline, self.source, self.source['market'])

    def test_reverse_alias_and_unavailable_isolation_check_rejected(self):
        for statuses in ((404, 200), (500, 404)):
            with patch.object(shadow.core, 'fetch_context', return_value=self.context), \
                 patch.object(shadow.core, 'http', side_effect=[(s, {}, {}) for s in statuses]):
                with self.assertRaisesRegex(ValueError, 'isolation'):
                    shadow.separate_context(self.backend, self.baseline, self.source, self.source['market'])

    def test_alignment_flags_divergent_portfolios_and_rejected_ai(self):
        candidate = self.plan()[0]
        result = shadow.alignment(candidate, self.body, self.ai_state['result'], self.context, self.source)
        self.assertEqual(result['classification'], 'ai_held')
        self.assertTrue(result['samePreDecisionCashAndInventory'])
        self.context['portfolio']['cash'] = 44.995
        self.body.update(action='BUY', symbol='BTCUSDT')
        self.ai_state['result']['response']['status'] = 'rejected'
        result = shadow.alignment(candidate, self.body, self.ai_state['result'], self.context, self.source)
        self.assertTrue(result['sameActionAndSymbol'])
        self.assertEqual(result['aiStatus'], 'rejected')
        self.assertFalse(result['samePreDecisionCashAndInventory'])

    def execute(self, dry_run=False):
        return shadow.execute(self.directory, self.source_dir, self.backend, self.baseline, dry_run)

    def test_forward_start_and_old_completed_slot_skip_without_submission(self):
        with patch.object(shadow.time, 'time', return_value=NOW - 3600), \
             patch.object(shadow, 'state_source') as load, patch.object(shadow.core, 'deliver') as post:
            self.assertEqual(self.execute()['status'], 'waiting')
            load.assert_not_called()
            post.assert_not_called()
        self.ai_state['slot'] -= 1
        with patch.object(shadow.time, 'time', return_value=NOW), \
             patch.object(shadow, 'state_source', return_value=(self.ai_state, self.source, self.body)), \
             patch.object(shadow.core, 'deliver') as post:
            self.assertEqual(self.execute()['status'], 'waiting')
            post.assert_not_called()

    def test_dry_run_never_consumes_slot_or_calls_delivery(self):
        with patch.object(shadow.time, 'time', return_value=NOW), \
             patch.object(shadow, 'state_source', return_value=(self.ai_state, self.source, self.body)), \
             patch.object(shadow, 'separate_context', return_value=self.context), \
             patch.object(shadow.core, 'deliver') as post:
            self.assertEqual(self.execute(True)['payload']['action'], 'BUY')
            post.assert_not_called()
        self.assertFalse((self.directory / 'state.json').exists())

    def test_saved_payload_uses_control_account_and_recovers_identically_after_timeout(self):
        actual_deliver = shadow.core.deliver
        calls = []
        def lost_response(method, url, body, key):
            calls.append((method, url, body, key))
            saved = shadow.core.read_json(self.directory / 'state.json')
            self.assertEqual((body, key), (saved['body'], saved['key']))
            self.assertEqual(json.loads(body)['contextId'], self.context['contextId'])
            self.assertEqual(json.loads(body)['strategyId'], 'trend-control')
            self.assertTrue(url.startswith(self.backend + '/'))
            raise TimeoutError('Committed but response lost')
        with patch.object(shadow.time, 'time', return_value=NOW), \
             patch.object(shadow, 'state_source', return_value=(self.ai_state, self.source, self.body)), \
             patch.object(shadow, 'separate_context', return_value=self.context), \
             patch.object(shadow.core, 'fetch_context', return_value=self.context), \
             patch.object(shadow.core, 'deliver', side_effect=lambda d, s: actual_deliver(d, s, lost_response, lambda: NOW, lambda _: None)):
            with self.assertRaises(RuntimeError):
                self.execute()
        expected = calls[0]
        def replay(method, url, body, key):
            self.assertEqual((method, url, body, key), expected)
            return 200, {'Idempotency-Replayed': 'true'}, {'status': 'executed', 'decisionId': str(uuid.uuid4())}
        with patch.object(shadow, 'state_source') as load, \
             patch.object(shadow.core, 'deliver', side_effect=lambda d, s: actual_deliver(d, s, replay, lambda: NOW, lambda _: None)):
            self.assertTrue(self.execute()['replayed'])
            load.assert_not_called()
        state = shadow.core.read_json(self.directory / 'state.json')
        result_path = Path(state['runDir']) / 'result.json'
        result_path.unlink()  # Crash between the complete-state write and audit copy.
        with patch.object(shadow.time, 'time', return_value=NOW):
            self.assertEqual(self.execute()['status'], 'skipped')
        self.assertEqual(shadow.core.read_json(result_path), state['result'])

    def test_expired_ai_snapshot_and_version_change_abort_before_order(self):
        with patch.object(shadow.time, 'time', return_value=NOW + 200), \
             patch.object(shadow, 'state_source', return_value=(self.ai_state, self.source, self.body)), \
             patch.object(shadow.core, 'deliver') as post:
            with self.assertRaisesRegex(ValueError, 'expired'):
                self.execute()
            post.assert_not_called()
        self.body['strategyVersion'] = 'changed'
        with patch.object(shadow.time, 'time', return_value=NOW), \
             patch.object(shadow, 'state_source', return_value=(self.ai_state, self.source, self.body)):
            with self.assertRaisesRegex(ValueError, 'AI strategy version changed'):
                self.execute()

    def test_completed_source_cannot_escape_runs_or_use_mismatched_context(self):
        self.ai_state['runDir'] = str(self.directory)
        shadow.core.save_json(self.source_dir / 'state.json', self.ai_state)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            shadow.state_source(self.source_dir, self.baseline)
        run_dir = self.source_dir / 'runs' / 'actual'
        run_dir.mkdir(parents=True)
        self.ai_state['runDir'] = str(run_dir)
        self.body['contextId'] = str(uuid.uuid4())
        self.ai_state['body'] = json.dumps(self.body)
        shadow.core.save_json(self.source_dir / 'state.json', self.ai_state)
        shadow.core.save_json(run_dir / 'snapshot.json', self.source)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            shadow.state_source(self.source_dir, self.baseline)

    def test_initialize_is_forward_only_and_idempotent(self):
        (self.directory / 'experiment.json').unlink()
        with patch.object(shadow.time, 'time', return_value=NOW), \
             patch.object(shadow, 'state_source', return_value=(self.ai_state, self.source, self.body)), \
             patch.object(shadow.core, 'collect_market', return_value=self.source['market']), \
             patch.object(shadow, 'separate_context', return_value=self.context):
            e = shadow.initialize(self.directory, self.source_dir, self.backend, self.baseline)
            self.assertEqual(e['startSlot'], SLOT + 1)
            self.assertEqual(shadow.initialize(self.directory, self.source_dir, self.backend, self.baseline), e)


if __name__ == '__main__':
    unittest.main()
