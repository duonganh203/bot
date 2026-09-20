"""V3 manifest, isolation, request recovery and portfolio-ownership boundaries."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import research_market as feed
import research_policy as policy
import research_runner as runner
from research_smoke import fixture


NOW = (1_800_000_000 // 21600) * 21600 + 120
SLOT = int(NOW) // 3600


class ResearchRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.market = fixture(NOW)
        self.context = {
            'contextId': str(uuid.uuid4()),
            'asOf': datetime.fromtimestamp(NOW, timezone.utc).isoformat(),
            'portfolio': {'version': 0, 'cash': 50, 'equity': 50, 'initialCapital': 50},
            'positions': [], 'recentTrades': [],
            'risk': {'policy': 'reduce-only-v2', 'reducingSellsAllowed': True,
                     'maxOrderUsd': 5, 'maxExposureUsd': 20, 'maxDailyLossUsd': 3,
                     'maxEquityLossUsd': 3, 'feeRate': 0.001,
                     'dailyLossLimitReached': False},
        }
        self.manifest = {
            'schemaVersion': 3, 'kind': 'research-v3', 'version': runner.version(),
            'createdAt': NOW - 3600, 'startSlot': SLOT,
            'backends': {'baseline': 'http://127.0.0.1:3004', 'candidate': 'http://127.0.0.1:3005'},
            'symbols': list(policy.SYMBOLS),
            'policies': {'baseline': deepcopy(runner.baseline.POLICY), 'candidate': deepcopy(policy.POLICY)},
            'aiFilter': False,
        }
        runner.core.save_json(self.root / 'experiment.json', self.manifest)
        (self.root / 'market').mkdir()
        self.event = {'schemaVersion': 3, 'slot': SLOT,
                      'marketId': feed.digest(self.market), 'market': self.market}
        runner.core.save_json(self.root / 'market' / (str(SLOT) + '.json'), self.event)

    def tearDown(self):
        self.temporary.cleanup()

    def execute(self, mode='candidate', dry=True, now=NOW):
        with patch.object(runner.time, 'time', return_value=now), \
                patch.object(runner.core, 'fetch_context', return_value=self.context):
            return runner.execute(self.root, mode, dry_run=dry)

    def saved(self, complete=True, status='held', slot=SLOT - 1):
        branch = self.root / 'candidate'
        run_dir = branch / 'runs' / 'previous'
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = runner.payload_for(policy.hold('Synthetic request'), self.context,
                                     self.market, 'candidate', self.manifest['version'])
        state = {
            'complete': complete, 'backend': self.manifest['backends']['candidate'],
            'slot': slot, 'slotSeconds': 3600, 'runDir': str(run_dir),
            'expiresAt': NOW + 120, 'key': str(uuid.uuid4()), 'body': runner.core.dumps(payload),
            'closing': [], 'marketId': self.event['marketId'], 'contextVersion': 0,
            'policyState': {'lastExitSlot': {}},
        }
        if complete:
            state['result'] = {'response': {'status': status, 'decisionId': str(uuid.uuid4())}}
        runner.core.save_json(branch / 'state.json', state)
        return state

    def test_both_branches_use_common_event_without_ai_or_dry_run_submission(self):
        with patch.object(runner.core, 'analyze', side_effect=AssertionError('AI called')), \
                patch.object(runner.core, 'deliver', side_effect=AssertionError('Dry run submitted')):
            baseline = self.execute('baseline')
            candidate = self.execute('candidate')
        for result in (baseline, candidate):
            self.assertEqual(result['payload']['action'], 'BUY')
            self.assertEqual(result['payload']['symbol'], 'BTCUSDT')
            evidence = runner.core.read_json(Path(result['runDir']) / 'evidence.json')
            self.assertEqual(evidence['marketId'], self.event['marketId'])
            self.assertIsNone(evidence['aiVote'])
        self.assertFalse((self.root / 'candidate' / 'state.json').exists())
        self.assertFalse((self.root / 'baseline' / 'state.json').exists())

    def test_manifest_policy_source_start_and_ai_changes_block_fresh_decisions(self):
        altered = []
        for key, value in [('version', 'different'), ('startSlot', SLOT - 1),
                           ('aiFilter', True), ('symbols', ['BTCUSDT'])]:
            manifest = deepcopy(self.manifest)
            manifest[key] = value
            altered.append(manifest)
        manifest = deepcopy(self.manifest)
        manifest['policies']['candidate']['entryIntervalHours'] = 1
        altered.append(manifest)
        for manifest in altered:
            with self.subTest(manifest=manifest):
                runner.core.save_json(self.root / 'experiment.json', manifest)
                with self.assertRaises(ValueError):
                    self.execute()

    def test_missing_stale_and_tampered_events_never_submit(self):
        path = self.root / 'market' / (str(SLOT) + '.json')
        path.unlink()
        self.assertEqual(self.execute()['status'], 'waiting')
        runner.core.save_json(path, self.event)
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.execute(now=NOW + 196)
        tampered = deepcopy(self.event)
        tampered['market']['symbols']['BTCUSDT']['price'] = '437'
        runner.core.save_json(path, tampered)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.execute()

    def test_out_of_band_portfolio_change_before_first_or_next_decision_blocks(self):
        self.context['portfolio']['version'] = 1
        with self.assertRaisesRegex(ValueError, 'before its first'):
            self.execute()
        self.saved(status='held')
        with self.assertRaisesRegex(ValueError, 'outside the research'):
            self.execute()
        self.saved(status='executed')
        self.assertEqual(self.execute()['status'], 'dry-run')

    def test_context_change_during_decision_blocks(self):
        updated = deepcopy(self.context)
        updated['portfolio']['version'] = 1
        with patch.object(runner.time, 'time', return_value=NOW), \
                patch.object(runner.core, 'fetch_context', side_effect=[self.context, updated]), \
                self.assertRaisesRegex(ValueError, 'during decision'):
            runner.execute(self.root, 'candidate')

    def test_pending_recovery_uses_original_request_despite_source_change(self):
        pending = self.saved(complete=False)
        self.manifest['version'] = 'source-changed'
        runner.core.save_json(self.root / 'experiment.json', self.manifest)
        with patch.object(runner.core, 'deliver', return_value={'recovered': True}) as deliver:
            result = self.execute(dry=False)
        self.assertEqual(result, {'recovered': True})
        self.assertEqual(deliver.call_args.args, (self.root / 'candidate', pending))
        with self.assertRaisesRegex(ValueError, 'pending outcome'):
            self.execute(dry=True)

    def test_saved_backend_and_run_path_cannot_be_redirected(self):
        pending = self.saved(complete=False)
        pending['backend'] = 'http://127.0.0.1:3999'
        runner.core.save_json(self.root / 'candidate' / 'state.json', pending)
        with self.assertRaisesRegex(ValueError, 'backend changed'):
            self.execute(dry=False)
        pending['backend'] = self.manifest['backends']['candidate']
        pending['runDir'] = str(self.root / 'baseline' / 'runs' / 'outside')
        runner.core.save_json(self.root / 'candidate' / 'state.json', pending)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.execute(dry=False)

    def test_completed_slot_skips_and_repairs_missing_audit_result(self):
        saved = self.saved(slot=SLOT)
        self.assertEqual(self.execute(dry=False)['status'], 'skipped')
        self.assertEqual(runner.core.read_json(Path(saved['runDir']) / 'result.json'), saved['result'])

    def test_exit_cooldown_only_follows_an_executed_full_sale(self):
        previous = self.saved(status='executed')
        previous['body'] = runner.core.dumps({'action': 'SELL', 'symbol': 'BTCUSDT'})
        previous['policyState'] = {'lastExitSlot': {'ETHUSDT': SLOT - 4}}
        untouched = deepcopy(previous)
        expected = {'lastExitSlot': {'ETHUSDT': SLOT - 4, 'BTCUSDT': SLOT - 1}}
        self.assertEqual(runner.policy_state(previous, self.context, self.market), expected)
        self.assertEqual(previous, untouched)
        self.context['positions'] = [{'symbol': 'BTCUSDT', 'quantity': '0.001'}]
        self.assertEqual(runner.policy_state(previous, self.context, self.market), previous['policyState'])
        self.context['positions'] = []
        for status in ('rejected', 'held'):
            previous['result']['response']['status'] = status
            self.assertEqual(runner.policy_state(previous, self.context, self.market), previous['policyState'])

    def test_initialize_checks_real_account_isolation_and_next_hour(self):
        new_root = self.root / 'fresh'
        other = deepcopy(self.context)
        other['contextId'] = str(uuid.uuid4())
        with patch.object(feed, 'collect_market', return_value=self.market), \
                patch.object(runner, 'checked_context', side_effect=[self.context, other]), \
                patch.object(runner.core, 'http', return_value=(404, {}, {})), \
                patch.object(runner.time, 'time', return_value=NOW):
            manifest = runner.initialize(new_root, self.manifest['backends'])
        self.assertEqual(manifest['startSlot'], SLOT + 1)
        with patch.object(runner.time, 'time', return_value=NOW):
            self.assertEqual(runner.execute(new_root, 'candidate')['status'], 'waiting')
        with patch.object(feed, 'collect_market', return_value=self.market), \
                patch.object(runner, 'checked_context', side_effect=[self.context, other]), \
                patch.object(runner.core, 'http', return_value=(200, {}, {})), \
                patch.object(runner.time, 'time', return_value=NOW), \
                self.assertRaisesRegex(ValueError, 'isolation'):
            runner.initialize(self.root / 'aliased', self.manifest['backends'])


if __name__ == '__main__':
    unittest.main()
