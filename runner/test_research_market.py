"""V3 market integrity checks: no lookahead, complete history, immutable input."""
from copy import deepcopy
from decimal import Decimal as D
import json
import math
from pathlib import Path
import statistics
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import research_market as market

SLOT = 600000
NOW = SLOT * 3600 + 120


def candles():
    return [[(SLOT - 337 + i) * market.HOUR_MS, str(100 + i), str(101 + i),
             str(99 + i), str(100 + i), '10'] for i in range(337)]


def fixture():
    rows = candles()
    quote = {'price': '436', 'quoteReceivedAt': NOW + 1, 'bookReceivedAt': NOW + 2,
             'bookBidPrice': '435.9', 'bookAskPrice': '436.1', 'candleColumns': list(market.COLUMNS),
             'closedHourlyCandles': rows, **market.features(rows)}
    return {'startedAt': NOW, 'completedAt': NOW + 3, 'serverTimeMs': NOW * 1000,
            'symbols': {symbol: deepcopy(quote) for symbol in market.SYMBOLS}}


class ResearchMarketTests(unittest.TestCase):
    def test_features_agree_with_independent_statistics(self):
        rows = candles()
        values = market.features(rows)
        self.assertEqual(D(values['sma72']), sum(D(row[4]) for row in rows[-72:]) / 72)
        self.assertEqual(D(values['return7dPct']), (D(436) / D(268) - 1) * 100)
        returns = [math.log(float(b[4]) / float(a[4])) for a, b in zip(rows, rows[1:])]
        expected = statistics.stdev(returns) * math.sqrt(24) * 100
        self.assertAlmostEqual(float(values['realizedVolDailyPct']), expected, places=11)

    def test_incomplete_current_candle_cannot_enter_features(self):
        rows = [row + [row[0] + market.HOUR_MS - 1] for row in candles()]
        rows.append([SLOT * market.HOUR_MS, '9000', '9000', '9000', '9000', '0',
                     (SLOT + 1) * market.HOUR_MS - 1])
        actual = market.closed_candles(rows, NOW * 1000)
        self.assertEqual(len(actual), 337)
        self.assertEqual(actual[-1][4], '436')
        self.assertEqual(market.features(actual)['closedPrice'], '436')

    def test_missing_duplicate_stale_and_future_candles_rejected(self):
        rows = candles()
        for damaged in [rows[:-1], rows[:50] + rows[51:], rows[:50] + [rows[49]] + rows[51:]]:
            with self.assertRaises(ValueError):
                market.features(damaged)
        data = fixture()
        for shift in (-market.HOUR_MS, market.HOUR_MS):
            changed = deepcopy(data)
            for row in changed['symbols']['BTCUSDT']['closedHourlyCandles']:
                row[0] += shift
            with self.assertRaisesRegex(ValueError, 'stale or looks ahead'):
                market.validate_market(changed)

    def test_invalid_ohlcv_rejected(self):
        for column, value in [(2, '1'), (3, '10000'), (4, 'NaN'), (5, '-1'), (0, True)]:
            rows = candles()
            rows[123][column] = value
            with self.assertRaises(ValueError):
                market.features(rows)

    def test_recomputes_old_and_slow_indicators(self):
        data = fixture()
        market.validate_market(data, NOW + 3)
        for key in market.FEATURES:
            changed = deepcopy(data)
            changed['symbols']['ETHUSDT'][key] = str(D(changed['symbols']['ETHUSDT'][key]) + 1)
            with self.assertRaisesRegex(ValueError, 'Indicator/candle mismatch'):
                market.validate_market(changed)

    def test_quote_collection_clock_freshness_and_book_validation(self):
        data = fixture()
        for key, value in [('quoteReceivedAt', NOW - 1), ('bookReceivedAt', NOW + 4),
                           ('bookBidPrice', '999'), ('price', '436.0000001')]:
            changed = deepcopy(data)
            changed['symbols']['BTCUSDT'][key] = value
            with self.assertRaises(ValueError):
                market.validate_market(changed)
        for key, value in [('startedAt', NOW - 31), ('completedAt', NOW + 60),
                           ('completedAt', (SLOT + 1) * 3600), ('serverTimeMs', True)]:
            changed = deepcopy(data)
            changed[key] = value
            with self.assertRaises(ValueError):
                market.validate_market(changed)
        with self.assertRaisesRegex(ValueError, 'expired'):
            market.validate_market(data, NOW + 240)
        with self.assertRaisesRegex(ValueError, 'future'):
            market.validate_market(data, NOW + 2)

    def test_collector_requests_338_rows_and_includes_book(self):
        raw = [row + [row[0] + market.HOUR_MS - 1] for row in candles()]
        raw.append([SLOT * market.HOUR_MS, '500', '500', '500', '500', '1', (SLOT + 1) * market.HOUR_MS - 1])
        requests = []

        def get_json(url):
            requests.append(url)
            query = parse_qs(urlsplit(url).query)
            symbol = query.get('symbol', [''])[0]
            if '/time' in url:
                return {'serverTime': NOW * 1000}
            if '/klines' in url:
                self.assertEqual(query['limit'], ['338'])
                return deepcopy(raw)
            if '/bookTicker' in url:
                return {'symbol': symbol, 'bidPrice': '435.9', 'askPrice': '436.1'}
            return {'symbol': symbol, 'price': '436'}

        core = SimpleNamespace(get_json=get_json, MARKET_URL='https://example.invalid', api_decimal=lambda x: x)
        with patch.object(market, '_core', return_value=core), patch.object(market.time, 'time', return_value=NOW):
            result = market.collect_market()
        self.assertEqual(len(requests), 16)
        self.assertEqual(result['symbols']['BTCUSDT']['bookAskPrice'], '436.1')
        market.validate_market(result, NOW)

    def test_event_checksum_and_publication_never_overwrite(self):
        data = fixture()
        calls = []

        def save_json(path, value):
            path.write_text(json.dumps(value), encoding='utf-8')

        core = SimpleNamespace(save_json=save_json, sync_directory=lambda directory: calls.append(directory))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(market, '_core', return_value=core), \
                 patch.object(market, 'collect_market', return_value=data), \
                 patch.object(market.time, 'time', return_value=NOW + 3):
                first = market.publish(directory)
                raw = (directory / (str(SLOT) + '.json')).read_bytes()
                second = market.publish(directory)
                self.assertEqual(first['status'], 'published')
                self.assertEqual(second['status'], 'skipped')
                self.assertEqual(raw, (directory / (str(SLOT) + '.json')).read_bytes())
            event = market.read_event(directory, SLOT)
            event['market']['symbols']['BTCUSDT']['price'] = '1'
            save_json(directory / (str(SLOT) + '.json'), event)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                market.read_event(directory, SLOT)
            self.assertEqual(len(list(directory.iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
