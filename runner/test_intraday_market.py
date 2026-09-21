"""V4 closed-candle integrity, delayed shared books and immutable publication."""
from copy import deepcopy
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import intraday_market as market

MINUTE = 30_000_000  # Aligned to UTC 4h, 1h and 15m boundaries.
NOW = MINUTE * 60 + 5
BAR = MINUTE // 15


def candles(interval):
    step, count = market.INTERVALS[interval]
    closed_boundary = NOW * 1000 // step * step
    return [[closed_boundary - (count - i) * step, str(100 + i), str(102 + i),
             str(98 + i), str(100 + i), '10'] for i in range(count)]


def feature_fixture():
    histories = {interval: candles(interval) for interval in market.INTERVALS}
    quote = {market.CANDLE_KEYS[interval]: rows for interval, rows in histories.items()}
    quote.update(market.features(histories['15m'], histories['1h'], histories['4h']))
    return {'schemaVersion': 4, 'bar': BAR, 'serverTimeMs': NOW * 1000,
            'startedAt': NOW, 'completedAt': NOW + 1, 'candleColumns': list(market.COLUMNS),
            'symbols': {symbol: deepcopy(quote) for symbol in market.SYMBOLS}}


def book_fixture(offset=2):
    quote = {'bid': '227.9', 'ask': '228.1', 'bidQty': '1000', 'askQty': '800',
             'receivedAt': NOW + offset + 0.5}
    return {'startedAt': NOW + offset, 'completedAt': NOW + offset + 1,
            'serverTimeMs': (NOW + offset) * 1000,
            'symbols': {symbol: deepcopy(quote) for symbol in market.SYMBOLS}}


def event_fixture():
    feature = feature_fixture()
    return {'schemaVersion': 4, 'minute': MINUTE, 'featureBar': BAR,
            'featureId': market.digest(feature), 'features': market.compact_features(feature),
            'decision': book_fixture(), 'execution': book_fixture(5)}


def fake_core():
    return SimpleNamespace(save_json=lambda path, value: path.write_text(json.dumps(value), encoding='utf-8'),
                           sync_directory=lambda directory: None)


class IntradayMarketTests(unittest.TestCase):
    def test_features_match_independent_ema_atr_and_sma_calculation(self):
        f = market.features(candles('15m'), candles('1h'), candles('4h'))
        prices = [float(row[4]) for row in candles('15m')]
        ema = sum(prices[:20]) / 20
        previous = None
        for price in prices[20:]:
            previous, ema = ema, ema * 19 / 21 + price * 2 / 21
        self.assertAlmostEqual(float(f['ema20_15m']), ema, places=11)
        self.assertAlmostEqual(float(f['previousEma20_15m']), previous, places=11)
        self.assertEqual(D(f['atr14_15m']), D(4))
        self.assertEqual(D(f['previousLow15m']), D(225))
        self.assertEqual(D(f['previousClose15m']), D(227))
        self.assertEqual(D(f['sma20_4h']), D('149.5'))
        self.assertEqual(D(f['sma50_4h']), D('134.5'))
        self.assertEqual(D(f['closedPrice4h']), D(159))
        self.assertEqual(D(f['closedPrice']), D(436))

    def test_atr_uses_previous_close_across_price_gaps(self):
        rows = candles('15m')
        for row in rows:
            row[1:5] = ['100', '100', '100', '100']
        rows[-1][1:5] = ['114', '114', '114', '114']
        f = market.features(rows, candles('1h'), candles('4h'))
        self.assertEqual(D(f['atr14_15m']), D(1))

    def test_incomplete_candles_excluded_in_every_timeframe(self):
        for interval, (step, _) in market.INTERVALS.items():
            normalized = candles(interval)
            raw = [row + [row[0] + step - 1] for row in normalized]
            opened = NOW * 1000 // step * step
            raw.append([opened, '9999', '9999', '9999', '9999', '0', opened + step - 1])
            self.assertEqual(market.closed_candles(raw, interval, NOW * 1000), normalized)

    def test_missing_duplicate_stale_lookahead_and_unaligned_candles_rejected(self):
        for interval, (step, _) in market.INTERVALS.items():
            rows = candles(interval)
            damaged_sets = [rows[:-1], rows[:25] + rows[26:], rows[:25] + [rows[24]] + rows[26:]]
            for shift in (-step, step, 1):
                shifted = deepcopy(rows)
                for row in shifted:
                    row[0] += shift
                damaged_sets.append(shifted)
            for damaged in damaged_sets:
                with self.assertRaises(ValueError):
                    market.checked_candles(damaged, interval, NOW * 1000)

    def test_invalid_candle_numbers_and_ohlc_rejected(self):
        for column, value in [(0, True), (1, '0'), (2, '1'), (3, '999'), (4, 'NaN'),
                              (4, 'Infinity'), (5, '-1'), (5, True)]:
            rows = candles('15m')
            rows[50][column] = value
            with self.assertRaises(ValueError):
                market.checked_candles(rows, '15m', NOW * 1000)

    def test_every_feature_recomputed_from_raw_candles(self):
        data = feature_fixture()
        market.validate_features(data)
        for key in market.FEATURE_KEYS:
            changed = deepcopy(data)
            changed['symbols']['BTCUSDT'][key] = str(D(changed['symbols']['BTCUSDT'][key]) + 1)
            with self.assertRaisesRegex(ValueError, 'Indicator/candle mismatch'):
                market.validate_features(changed)

    def test_feature_clock_duration_and_bar_checks(self):
        original = feature_fixture()
        for key, value in [('startedAt', NOW - 31), ('completedAt', NOW + 45),
                           ('serverTimeMs', True), ('bar', BAR - 1)]:
            data = deepcopy(original)
            data[key] = value
            with self.assertRaises(ValueError):
                market.validate_features(data)
        data = deepcopy(original)
        data['startedAt'] = (BAR + 1) * 900 - 1
        data['completedAt'] = (BAR + 1) * 900 + 1
        data['serverTimeMs'] = data['startedAt'] * 1000
        with self.assertRaisesRegex(ValueError, 'crossed 15-minute bar'):
            market.validate_features(data)

    def test_collector_freezes_closed_end_time_and_requests_all_three_intervals(self):
        requests = []

        def get_json(url):
            requests.append(url)
            if '/time' in url:
                return {'serverTime': NOW * 1000}
            query = parse_qs(urlsplit(url).query)
            interval = query['interval'][0]
            step, count = market.INTERVALS[interval]
            self.assertEqual(query['limit'], [str(count)])
            self.assertEqual(query['endTime'], [str(NOW * 1000 // step * step - 1)])
            return [row + [row[0] + step - 1] for row in candles(interval)]

        core = SimpleNamespace(get_json=get_json, MARKET_URL='https://example.invalid')
        with patch.object(market, '_core', return_value=core), patch.object(market.time, 'time', return_value=NOW):
            result = market.collect_features()
        self.assertEqual(len(requests), 16)
        self.assertEqual(result['bar'], BAR)
        self.assertEqual(len(result['symbols']['BTCUSDT']['closed4hCandles']), 60)

    def test_book_collector_records_best_prices_quantities_and_receive_time(self):
        requests = []

        def get_json(url):
            requests.append(url)
            if '/time' in url:
                return {'serverTime': NOW * 1000}
            symbol = parse_qs(urlsplit(url).query)['symbol'][0]
            return {'symbol': symbol, 'bidPrice': '99.9', 'askPrice': '100.1', 'bidQty': '5', 'askQty': '8'}

        core = SimpleNamespace(get_json=get_json, MARKET_URL='https://example.invalid')
        with patch.object(market, '_core', return_value=core), patch.object(market.time, 'time', return_value=NOW):
            result = market.collect_book()
        self.assertEqual(len(requests), 6)
        self.assertEqual(result['symbols']['ETHUSDT'], {'bid': '99.9', 'ask': '100.1',
                         'bidQty': '5', 'askQty': '8', 'receivedAt': NOW})

    def test_invalid_crossed_empty_book_and_receive_time_rejected(self):
        for key, value in [('bid', '999'), ('ask', '0'), ('bidQty', '0'), ('askQty', '-1'),
                           ('askQty', 'NaN'), ('receivedAt', NOW - 1), ('receivedAt', NOW + 59)]:
            book = book_fixture()
            book['symbols']['SOLUSDT'][key] = value
            with self.assertRaises(ValueError):
                market.validate_book(book)
        book = book_fixture()
        book['completedAt'] = (MINUTE + 1) * 60
        with self.assertRaises(ValueError):
            market.validate_book(book)

    def test_execution_delay_feature_availability_and_minute_freshness(self):
        original = event_fixture()
        market.validate_event(original, NOW + 6)
        event = deepcopy(original)
        event['execution'] = book_fixture(4)
        with self.assertRaisesRegex(ValueError, 'minimum delay'):
            market.validate_event(event)
        event = deepcopy(original)
        event['features']['completedAt'] = NOW + 3
        with self.assertRaisesRegex(ValueError, 'Features completed after'):
            market.validate_event(event)
        event = deepcopy(original)
        event['features']['serverTimeMs'] = (NOW + 3) * 1000
        with self.assertRaisesRegex(ValueError, 'Feature server time after'):
            market.validate_event(event)
        for now in [NOW + 5, (MINUTE + 1) * 60]:
            with self.assertRaisesRegex(ValueError, 'expired or from the future'):
                market.validate_event(original, now)
        event = deepcopy(original)
        event['featureBar'] -= 1
        with self.assertRaisesRegex(ValueError, 'different feature bar'):
            market.validate_event(event)

    def test_immutable_publication_compact_resolution_and_cached_feature_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(market, '_core', return_value=fake_core()), \
                 patch.object(market, 'collect_features', return_value=feature_fixture()) as collect, \
                 patch.object(market, 'collect_book', side_effect=[book_fixture(), book_fixture(5)]), \
                 patch.object(market.time, 'time', return_value=NOW + 6), \
                 patch.object(market.time, 'sleep') as sleep:
                first = market.publish(root)
                path = root / (str(MINUTE) + '.json')
                saved = path.read_bytes()
                second = market.publish(root)
            self.assertEqual(first['status'], 'published')
            self.assertEqual(second['status'], 'skipped')
            self.assertEqual(path.read_bytes(), saved)
            collect.assert_called_once()
            sleep.assert_called_once_with(2)
            disk = json.loads(saved)
            self.assertNotIn('features', disk)
            cache = {}
            with patch.object(market, 'validate_features', wraps=market.validate_features) as validate:
                actual = market.read_event(root, MINUTE, cache)
                market.read_event(root, MINUTE, cache)
                self.assertEqual(validate.call_count, 1)
            self.assertEqual(actual['features']['symbols']['BTCUSDT']['closedPrice15m'], '228')
            self.assertNotIn('closed15mCandles', actual['features']['symbols']['BTCUSDT'])
            self.assertEqual(len(list(root.glob('.*'))), 0)

    def test_tampered_event_feature_and_reference_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(market, '_core', return_value=fake_core()), \
                 patch.object(market, 'collect_features', return_value=feature_fixture()), \
                 patch.object(market, 'collect_book', side_effect=[book_fixture(), book_fixture(5)]), \
                 patch.object(market.time, 'time', return_value=NOW + 6), patch.object(market.time, 'sleep'):
                market.publish(root)
            event_path = root / (str(MINUTE) + '.json')
            feature_path = root / 'features' / (str(BAR) + '.json')
            original_event, original_feature = event_path.read_bytes(), feature_path.read_bytes()
            event = json.loads(original_event)
            event['execution']['symbols']['BTCUSDT']['bid'] = '1'
            event_path.write_text(json.dumps(event), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Minute event checksum'):
                market.read_event(root, MINUTE)
            event_path.write_bytes(original_event)
            feature = json.loads(original_feature)
            feature['symbols']['BTCUSDT']['ema20_15m'] = '1'
            feature_path.write_text(json.dumps(feature), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Feature checksum'):
                market.read_event(root, MINUTE)
            feature_path.write_bytes(original_feature)
            event = json.loads(original_event)
            event.pop('marketId')
            event['featureId'] = '0' * 64
            event['marketId'] = market.digest(event)
            event_path.write_text(json.dumps(event), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'feature checksum'):
                market.read_event(root, MINUTE)
            feature_path.unlink()
            with self.assertRaisesRegex(ValueError, 'feature artifact missing'):
                market.read_event(root, MINUTE)

    def test_atomic_publish_loser_keeps_original_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / '1.json'
            with patch.object(market, '_core', return_value=fake_core()):
                self.assertTrue(market._publish_once(path, {'winner': 1}))
                self.assertFalse(market._publish_once(path, {'winner': 2}))
            self.assertEqual(json.loads(path.read_text()), {'winner': 1})
            self.assertEqual(list(root.iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
