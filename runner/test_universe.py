"""Universe comparison must change eligible assets, not risk or AI behavior."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paired
import policy
import market_snapshot as feed
from test_shadow import fixture, NOW, SLOT


class UniverseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.context, source, _, _ = fixture()
        self.market = source['market']
        for symbol in policy.UNIVERSES['five'][2:]:
            self.market['symbols'][symbol] = deepcopy(self.market['symbols']['BTCUSDT'])
        self.context['risk'].update(policy='reduce-only-v2', maxEquityLossUsd=3, reducingSellsAllowed=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_wider_universe_adds_sol_after_btc_eth_and_keeps_caps_and_exits(self):
        self.context['portfolio']['cash'] = '39.99'
        self.context['positions'] = [{'symbol': s, 'quantity': '0.025'} for s in policy.SYMBOLS]
        self.assertEqual(policy.plan(self.context, self.market)[0]['action'], 'HOLD')
        wider = lambda: policy.plan(self.context, self.market, symbols=policy.UNIVERSES['five'])
        self.assertEqual(wider()[0]['symbol'], 'SOLUSDT')
        self.context['positions'].append({'symbol': 'SOLUSDT', 'quantity': '0.025'})
        self.context['portfolio']['cash'] = '34.985'
        self.assertEqual(wider()[0]['symbol'], 'BNBUSDT')
        self.context['positions'].append({'symbol': 'BNBUSDT', 'quantity': '0.025'})
        self.context['portfolio']['cash'] = '29.98'
        self.assertEqual(wider()[3], 'MAX_EXPOSURE')
        self.market['symbols']['SOLUSDT'].update(price='100', return24hPct='-1')
        self.assertEqual((wider()[0]['action'], wider()[0]['symbol']), ('SELL', 'SOLUSDT'))
        with self.assertRaisesRegex(ValueError, 'outside'):
            policy.plan(self.context, self.market)

    def test_fresh_universes_share_input_but_never_call_ai(self):
        urls = {'two': 'http://localhost:3002', 'five': 'http://localhost:3003'}
        with patch.object(paired.core, 'collect_market', return_value=self.market), \
             patch.object(paired.core, 'fetch_context', return_value=self.context), \
             patch.object(paired.core, 'http', return_value=(404, {}, {})), \
             patch.object(paired.time, 'time', return_value=NOW):
            experiment = paired.initialize(self.root, urls, policy.UNIVERSES)
        self.assertEqual(experiment['startSlot'], SLOT + 1)
        self.assertFalse(experiment['aiFilter'])
        experiment['startSlot'] = SLOT
        paired.core.save_json(self.root / 'experiment.json', experiment)
        (self.root / 'market').mkdir()
        self.market['symbols']['BTCUSDT']['price'] = '100'
        self.market['symbols']['ETHUSDT']['price'] = '100'
        paired.core.save_json(self.root / 'market' / (str(SLOT) + '.json'), {
            'schemaVersion': 2, 'slot': SLOT, 'marketId': feed.digest(self.market), 'market': self.market})
        with patch.object(paired.time, 'time', return_value=NOW), \
             patch.object(paired.core, 'fetch_context', return_value=self.context), \
             patch.object(paired.entry_filter, 'vote', side_effect=AssertionError('AI called')):
            self.assertEqual(paired.execute(self.root, 'two', True)['payload']['action'], 'HOLD')
            five = paired.execute(self.root, 'five', True)['payload']
        self.assertEqual((five['action'], five['symbol'], five['amountUsd']), ('BUY', 'SOLUSDT', '5'))
        self.assertFalse((self.root / 'five' / 'state.json').exists())


if __name__ == '__main__':
    unittest.main()
