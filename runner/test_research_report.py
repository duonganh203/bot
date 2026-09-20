"""Accounting, prospective boundaries and audit integrity for the V3 report."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import research_report as report


SLOT = 500000
NOW = SLOT * 3600 + 400


class ResearchReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.experiment = {'schemaVersion': 3, 'kind': 'research-v3', 'version': 'v3-test', 'startSlot': SLOT,
                           'symbols': list(report.core.SUPPORTED_SYMBOLS),
                           'backends': {'baseline': 'http://localhost:3004', 'candidate': 'http://localhost:3005'}}
        self.write(self.root / 'experiment.json', self.experiment)
        self.market = self.make_market(SLOT, '120')
        context = {'portfolio': {'initialCapital': '50', 'cash': '50', 'equity': '50', 'totalFees': '0'},
                   'positions': [], 'risk': {}}
        self.contexts = {mode: deepcopy(context) for mode in report.MODES}
        self.event(SLOT, '100')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def make_market(self, slot, price):
        return {'startedAt': slot * 3600 + 1, 'serverTimeMs': (slot * 3600 + 1) * 1000,
                'symbols': {s: {'price': price, 'bookBidPrice': '99', 'bookAskPrice': '101'}
                            for s in report.core.SUPPORTED_SYMBOLS}}

    def event(self, slot, price):
        event = {'schemaVersion': 3, 'slot': slot, 'marketId': 'market-' + str(slot),
                 'market': self.make_market(slot, price)}
        self.write(self.root / 'market' / (str(slot) + '.json'), event)
        return event

    def request(self, mode, slot=SLOT, status='held', action='HOLD', equity='50', fee='0.005'):
        event = report.core.read_json(self.root / 'market' / (str(slot) + '.json'))
        folder = self.root / mode / 'runs' / (str(slot) + '-test')
        candidate = {'action': action, 'symbol': 'BTCUSDT' if action != 'HOLD' else None,
                     'amountUsd': '5' if action != 'HOLD' else None}
        evidence = {'slot': slot, 'mode': mode, 'marketId': event['marketId'], 'version': 'v3-test',
                    'market': event['market'], 'context': {'portfolio': {'equity': equity}},
                    'gates': {s: {'holdingUsd': '0'} for s in self.experiment['symbols']},
                    'candidate': candidate, 'ruleCandidate': candidate, 'aiVote': None}
        request = {'slot': slot, 'marketId': event['marketId'], 'key': mode + '-' + str(slot),
                   'expiresAt': slot * 3600 + 240}
        self.write(folder / 'request.json', request)
        self.write(folder / 'evidence.json', evidence)
        if status is not None:
            response = {'status': status}
            if status == 'executed':
                response['trade'] = {'grossUsd': '5', 'feeUsd': fee,
                    'createdAt': datetime.fromtimestamp(slot * 3600 + 30, timezone.utc).isoformat()}
            self.write(folder / 'result.json', {'response': response})
        return folder

    def summarize(self, now=NOW, cost='0'):
        # The feed owns event checksum/candle validation; this isolates report accounting.
        def event_reader(directory, slot):
            path = directory / (str(slot) + '.json')
            return report.core.read_json(path) if path.exists() else None
        with patch.object(report.feed, 'read_event', side_effect=event_reader):
            return report.summarize(self.root, now, cost, self.market, self.contexts)

    def test_net_equity_fees_drawdown_and_turnover_costs_do_not_double_count(self):
        self.request('baseline')
        self.request('candidate', status='executed', action='BUY')
        self.contexts['candidate']['portfolio'].update(cash='44.995', equity='50.995', totalFees='0.005')
        self.contexts['candidate']['positions'] = [{'symbol': 'BTCUSDT', 'quantity': '0.05'}]
        result = self.summarize()
        candidate = result['accounts']['candidate']
        self.assertEqual(D(candidate['netPnlUsd']), D('0.995'))
        self.assertEqual(D(candidate['feesPaidUsd']), D('0.005'))
        self.assertEqual(D(candidate['feeReconciliationDifferenceUsd']), 0)
        self.assertEqual(D(candidate['observedMaxDrawdownPct']), D('0.01'))
        self.assertEqual(D(candidate['grossTurnoverUsd']), 5)
        self.assertEqual(D(candidate['extraExecutionCostEstimateUsd']['5']), D('0.0025'))
        self.assertEqual(D(candidate['costAdjustedScenarios']['10']['netEquityUsd']), D('50.990'))
        self.assertEqual(result['matchedMarketSlots'], 1)
        self.assertEqual(result['sameRuleProposalSlots'], 0)
        self.assertEqual(result['operatingCost']['status'], 'not_measured')
        self.assertFalse(result['promotion']['eligible'])

    def test_benchmarks_use_first_scheduled_event_and_entry_fee_only(self):
        self.event(SLOT - 1, '1')
        self.request('baseline', slot=SLOT - 1, status='executed', action='BUY', fee='5')
        result = self.summarize()
        for name in ('btc20_cash', 'equal_weight_five20_cash'):
            benchmark = result['benchmarks'][name]
            self.assertEqual(D(benchmark['netEquityUsd']), D('53.98'))
            self.assertEqual(D(benchmark['entryFeeUsd']), D('0.02'))
            self.assertEqual(D(benchmark['returnPct']), D('7.96'))
        self.assertEqual(result['benchmarks']['startMarketId'], 'market-' + str(SLOT))
        self.assertEqual(result['accounts']['baseline']['completedSlots'], 0)
        self.assertEqual(D(result['accounts']['baseline']['grossTurnoverUsd']), 0)
        self.assertEqual(result['measuredBookSpread']['bySymbol']['BTCUSDT']['samples'], 1)
        self.assertEqual(D(result['measuredBookSpread']['bySymbol']['BTCUSDT']['meanBps']), 200)

    def test_missing_initial_market_does_not_move_benchmark_start(self):
        (self.root / 'market' / (str(SLOT) + '.json')).unlink()
        self.event(SLOT + 1, '110')
        result = self.summarize(NOW + 3600)
        self.assertEqual(result['benchmarks']['btc20_cash']['status'], 'unavailable')
        self.assertEqual(result['benchmarks']['cash']['status'], 'available')
        self.assertEqual(result['missingMarketSlots'], [SLOT])

    def test_missing_pending_and_rejected_slots_are_separate(self):
        self.request('baseline', status='rejected', action='BUY')
        self.request('candidate', status=None, action='BUY')
        result = self.summarize(NOW + 7200)
        baseline, candidate = (result['accounts'][s] for s in report.MODES)
        self.assertEqual(baseline['rejectedRequests'], 1)
        self.assertEqual(baseline['completedSlots'], 1)
        self.assertEqual(baseline['missingSlots'], [SLOT + 1, SLOT + 2])
        self.assertEqual(candidate['missingSlots'], [SLOT + 1, SLOT + 2])
        self.assertEqual(candidate['incompleteSlots'], [SLOT, SLOT + 1, SLOT + 2])
        self.assertTrue(candidate['pendingRequests'][0]['expired'])
        self.assertEqual(D(baseline['grossTurnoverUsd']), 0)

    def test_monthly_operating_cost_is_an_explicit_prorated_assumption(self):
        now = SLOT * 3600 + 86400
        result = self.summarize(now, '30')
        self.assertEqual(result['operatingCost']['status'], 'assumption_not_measured')
        self.assertEqual(D(result['operatingCost']['assumedCostUsdPerBranch']), 1)
        self.assertEqual(D(result['accounts']['candidate']['costAdjustedScenarios']['0']['netEquityUsd']), 49)
        for bad in ('-1', 'NaN', 'Infinity'):
            with self.assertRaises(ValueError):
                self.summarize(cost=bad)

    def test_invalid_manifest_is_rejected_before_fetching_data(self):
        variants = [{'schemaVersion': 2}, {'kind': 'universe-comparison'}, {'version': ''},
                    {'startSlot': True}, {'symbols': ['BTCUSDT']},
                    {'backends': {'baseline': 'same', 'candidate': 'same'}}]
        for changes in variants:
            with self.subTest(changes=changes):
                self.write(self.root / 'experiment.json', {**self.experiment, **changes})
                with patch.object(report.feed, 'collect_market', side_effect=AssertionError('Unexpected network')):
                    with self.assertRaises(ValueError):
                        report.summarize(self.root, NOW)

    def test_evidence_tampering_mixed_version_and_ai_vote_are_rejected(self):
        folder = self.request('baseline')
        original = report.core.read_json(folder / 'evidence.json')
        variants = [{'marketId': 'different'}, {'version': 'other'}, {'mode': 'candidate'},
                    {'aiVote': {'approve': True}}, {'market': self.make_market(SLOT, '999')}]
        for changes in variants:
            with self.subTest(changes=changes):
                self.write(folder / 'evidence.json', {**original, **changes})
                with self.assertRaises(ValueError):
                    self.summarize()

    def test_recovered_result_and_ledger_fee_difference_are_visible(self):
        folder = self.request('candidate', status=None, action='BUY')
        request = report.core.read_json(folder / 'request.json')
        result = {'response': {'status': 'executed', 'trade': {'grossUsd': '5', 'feeUsd': '0.005',
                  'createdAt': datetime.fromtimestamp(SLOT * 3600 + 30, timezone.utc).isoformat()}}}
        self.write(self.root / 'candidate' / 'state.json', {**request, 'complete': True, 'result': result})
        self.contexts['candidate']['portfolio'].update(equity='49.995', totalFees='0.010')
        account = self.summarize()['accounts']['candidate']
        self.assertEqual(account['completedSlots'], 1)
        self.assertEqual(account['pendingRequests'], [])
        self.assertEqual(D(account['feeReconciliationDifferenceUsd']), D('0.005'))


if __name__ == '__main__':
    unittest.main()
