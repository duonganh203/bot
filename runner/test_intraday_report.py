"""Forward accounting, coverage and evidence boundaries for the V4 report."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import intraday_report as report

MINUTE = 30_000_000
NOW = (MINUTE + 2) * 60 + 10


class IntradayReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest = {'schemaVersion': 4, 'kind': 'intraday-v4', 'version': 'v4-test',
            'startMinute': MINUTE, 'startBar': MINUTE // 15, 'symbols': list(report.SYMBOLS),
            'backends': {'slow': 'http://localhost:3006', 'pullback': 'http://localhost:3007'}}
        self.write(self.root / 'experiment.json', self.manifest)
        self.book = {'startedAt': NOW, 'completedAt': NOW + 1, 'serverTimeMs': NOW * 1000,
            'symbols': {s: {'bid': '100', 'ask': '102', 'bidQty': '10', 'askQty': '10', 'receivedAt': NOW + 1}
                        for s in report.SYMBOLS}}
        context = {'portfolio': {'initialCapital': '50', 'cash': '50', 'equity': '50', 'totalFees': '0', 'version': 0},
                   'positions': [], 'risk': {}}
        self.contexts = {m: deepcopy(context) for m in report.MODES}
        for minute in (MINUTE, MINUTE + 1):
            self.event(minute)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def event(self, minute):
        event = {'minute': minute, 'marketId': 'market-' + str(minute), 'featureId': 'feature-' + str(minute // 15)}
        self.write(self.root / 'market' / (str(minute) + '.json'), event)
        return event

    def cycle(self, mode, minute=MINUTE, actions=None, status='complete'):
        event = self.event(minute)
        cycle = {**event, 'mode': mode, 'version': 'v4-test', 'status': status,
            'reason': 'NO_ELIGIBLE_ENTRY', 'actions': actions or [],
            'gates': {'BTCUSDT': {'failedGates': ['closed4hAboveSma20']}},
            'context': deepcopy(self.contexts[mode]) if status == 'complete' else None}
        self.write(self.root / mode / 'cycles' / (str(minute) + '.json'), cycle)
        return cycle

    def action(self, side='BUY', quantity='0.05', price='102.0204', identifier='buy'):
        gross = D(quantity) * D(price)
        return {'status': 'executed', 'action': side, 'symbol': 'BTCUSDT', 'decisionId': identifier,
            'trade': {'symbol': 'BTCUSDT', 'side': side, 'quantity': quantity, 'price': price,
                'grossUsd': str(gross), 'feeUsd': str(gross * D('.001')),
                'createdAt': datetime.fromtimestamp(MINUTE * 60 + 20, timezone.utc).isoformat()},
            'execution': {'executionBid': '100', 'executionAsk': '102',
                'executionReferencePrice': '102' if side == 'BUY' else '100',
                'decisionQuoteReceivedAt': MINUTE * 60 + 5}}

    def build(self, now=NOW, cost='0'):
        with patch.object(report.feed, 'read_event', side_effect=lambda root, minute, feature_cache=None:
                          report.read_json(root / (str(minute) + '.json'))):
            return report.build_report(self.root, now, self.book, self.contexts, cost)

    def test_bid_equity_fees_and_execution_costs_are_not_double_counted(self):
        action = self.action()
        fee = D(action['trade']['feeUsd'])
        self.contexts['pullback']['portfolio'].update(cash='44.89387898', equity='99', totalFees=str(fee), version=1)
        self.contexts['pullback']['positions'] = [{'symbol': 'BTCUSDT', 'quantity': '.05', 'marketValue': '5'}]
        self.cycle('pullback', actions=[action])
        self.cycle('slow')
        result = self.build()
        account = result['accounts']['pullback']
        self.assertEqual(D(account['netEquityUsd']), D('49.89387898'))
        self.assertEqual(D(account['feeReconciliationDifferenceUsd']), 0)
        self.assertEqual(D(account['reportedEmbeddedSpreadCostUsd']), D('.05'))
        self.assertEqual(D(account['reportedEmbeddedSlippageCostUsd']), D('.001020'))
        self.assertEqual(account['meanDecisionToFillSeconds'], 15)
        self.assertEqual(D(account['costAdjustedScenarios']['0']['netEquityUsd']), D('49.89387898'))
        self.assertEqual(result['matchedMarketMinutes'], 1)

    def test_interrupted_cycle_retains_fills_but_is_incomplete(self):
        action = self.action()
        self.contexts['pullback']['portfolio'].update(totalFees=action['trade']['feeUsd'], version=1)
        self.cycle('pullback', actions=[action], status='interrupted')
        account = self.build()['accounts']['pullback']
        self.assertEqual(account['completedMinutes'], 0)
        self.assertEqual(account['interruptedMinutes'], [MINUTE])
        self.assertIn(MINUTE, account['incompleteMinutes'])
        self.assertEqual(account['fills'], {'BUY': 1})
        self.assertEqual(D(account['feeReconciliationDifferenceUsd']), 0)

    def test_missing_pending_and_current_minute_are_distinct(self):
        self.cycle('slow')
        self.write(self.root / 'pullback' / 'state.json', {'pending': {'directory': 'pending'}, 'cycle': {'minute': MINUTE}})
        result = self.build()
        slow, candidate = (result['accounts'][m] for m in report.MODES)
        self.assertEqual(result['dueMinutes'], 2)
        self.assertEqual(slow['missingMinutes'], [MINUTE + 1])
        self.assertEqual(candidate['missingMinutes'], [MINUTE + 1])
        self.assertEqual(candidate['incompleteMinutes'], [MINUTE, MINUTE + 1])

    def test_gate_diagnostics_count_once_per_feature_bar(self):
        self.cycle('pullback')
        self.cycle('pullback', MINUTE + 1)
        account = self.build()['accounts']['pullback']
        self.assertEqual(account['observedEntryFeatureBars'], 1)
        self.assertEqual(account['failedEntryGatesBySymbolPerBar'], {'BTCUSDT:closed4hAboveSma20': 1})

    def test_prospective_boundary_excludes_preflight(self):
        self.cycle('pullback', MINUTE - 1, [self.action()])
        result = self.build()
        self.assertEqual(result['accounts']['pullback']['fills'], {})
        self.assertEqual(result['accounts']['pullback']['loggedFeesUsd'], '0')

    def test_round_trip_requires_complete_exit(self):
        self.contexts['pullback']['portfolio']['version'] = 3
        self.cycle('pullback', actions=[self.action(), self.action('SELL', '.02', '99.98', 'sell-one')])
        self.cycle('pullback', MINUTE + 1, [self.action('SELL', '.03', '99.98', 'sell-two')])
        result = self.build()['accounts']['pullback']
        self.assertEqual(result['completedRoundTrips'], 1)
        self.assertEqual(result['fills'], {'BUY': 1, 'SELL': 2})

    def test_evidence_mismatch_duplicate_decisions_and_versions_fail(self):
        original = self.cycle('slow')
        path = self.root / 'slow' / 'cycles' / (str(MINUTE) + '.json')
        for change in ({'marketId': 'other'}, {'featureId': 'other'}, {'version': 'other'}, {'mode': 'pullback'},
                       {'aiVote': {'approve': True}}):
            self.write(path, {**original, **change})
            with self.assertRaises(ValueError):
                self.build()
        self.contexts['slow']['portfolio']['version'] = 2
        self.cycle('slow', actions=[self.action(), self.action()])
        with self.assertRaisesRegex(ValueError, 'Duplicate decision'):
            self.build()

    def test_cost_assumption_and_review_are_not_promotion(self):
        now = MINUTE * 60 + 14 * 86400
        result = self.build(now, '30')
        self.assertEqual(D(result['operatingCost']['assumedCostUsdPerBranch']), 14)
        self.assertEqual(D(result['accounts']['slow']['costAdjustedScenarios']['0']['netEquityUsd']), 36)
        self.assertTrue(result['review']['fourteenDaysElapsed'])
        self.assertFalse(result['review']['automaticPromotion'])
        self.assertEqual(result['review']['status'], 'manual_review_required')
        for value in ('-1', 'NaN', 'Infinity'):
            with self.assertRaises(ValueError):
                self.build(cost=value)

    def test_invalid_manifest_fails_before_network(self):
        for change in ({'schemaVersion': 3}, {'startMinute': True}, {'startBar': 0}, {'version': ''},
                       {'backends': {'slow': 'same', 'pullback': 'same'}}):
            self.write(self.root / 'experiment.json', {**self.manifest, **change})
            with patch.object(report.feed, 'collect_book', side_effect=AssertionError('Unexpected network')):
                with self.assertRaises(ValueError):
                    report.build_report(self.root)


if __name__ == '__main__':
    unittest.main()
