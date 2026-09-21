#!/usr/bin/env python3
"""Immutable V4 minute books and shared, strictly closed 15m/1h/4h features.

Book timestamps are local HTTP receive times, not exchange execution times.
The second book is observed at least two seconds after the first collection.
Raw candle histories are stored once per 15-minute feature snapshot; minute
events reference that immutable snapshot instead of duplicating its history.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlencode
import uuid

import research_market as hourly
from research_policy import SYMBOLS

MINUTE_MS = 60_000
BAR_MS = 900_000
HOUR_MS = 3_600_000
FOUR_HOUR_MS = 14_400_000
COLUMNS = list(hourly.COLUMNS)
INTERVALS = {'15m': (BAR_MS, 129), '1h': (HOUR_MS, 337), '4h': (FOUR_HOUR_MS, 60)}
CANDLE_KEYS = {'15m': 'closed15mCandles', '1h': 'closedHourlyCandles', '4h': 'closed4hCandles'}
FEATURE_KEYS = (
    'closedPrice15m', 'previousClose15m', 'previousLow15m',
    'previousEma20_15m', 'ema20_15m', 'atr14_15m',
    'closedPrice4h', 'sma20_4h', 'sma50_4h',
    'closedPrice', 'sma72', 'sma168', 'return24hPct', 'return7dPct', 'realizedVolDailyPct',
)
MIN_EXECUTION_DELAY_SECONDS = 2
MAX_BOOK_COLLECTION_SECONDS = 20
MAX_FEATURE_COLLECTION_SECONDS = 45
MAX_EVENT_AGE_SECONDS = 60


def _core():
    import run
    return run


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value, positive=False, price=False):
    require(not isinstance(value, bool), 'Boolean is not a market number')
    try:
        number = D(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError('Invalid market number') from error
    require(number.is_finite(), 'Market number must be finite')
    require(number > 0 if positive else number >= 0, 'Invalid market price/quantity')
    if price:
        require(number <= D('1000000000'), 'Price exceeds API bounds')
    return number


def _integer(value, label):
    require(isinstance(value, int) and not isinstance(value, bool) and value >= 0,
            'Invalid ' + label)
    return value


def _timestamp(value, label):
    require(isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0, 'Invalid ' + label)
    return value


def _check_collection(data, maximum):
    start = _timestamp(data['startedAt'], 'collection start')
    end = _timestamp(data['completedAt'], 'collection completion')
    server_ms = _integer(data['serverTimeMs'], 'server time')
    require(0 <= end - start < maximum, 'Collection took too long or reversed')
    require(abs(server_ms / 1000 - start) <= 30, 'Market and collector clocks differ by over 30s')
    require(set(data['symbols']) == set(SYMBOLS), 'V4 requires fixed five-symbol universe')
    return start, end, server_ms


def checked_candles(candles, interval, server_ms=None):
    step, count = INTERVALS[interval]
    require(isinstance(candles, list) and len(candles) == count,
            f'Need exactly {count} closed {interval} candles')
    previous = None
    for row in candles:
        require(isinstance(row, list) and len(row) == 6, 'Malformed normalized candle')
        opened = _integer(row[0], 'candle timestamp')
        require(opened % step == 0, 'Candle is not aligned to UTC interval')
        if previous is not None:
            require(opened - previous == step, 'Candles duplicated, out of order, or have gaps')
        previous = opened
        opening, high, low, close = (_number(row[i], True, True) for i in range(1, 5))
        _number(row[5])
        require(low <= min(opening, close) <= max(opening, close) <= high, 'Invalid OHLCV values')
    if server_ms is not None:
        _integer(server_ms, 'server time')
        require(candles[-1][0] == (server_ms // step - 1) * step,
                'Latest closed candle is stale or looks ahead')
    return candles


def closed_candles(rows, interval, server_ms):
    """Normalize and reject gaps; an in-progress candle never enters features."""
    step, count = INTERVALS[interval]
    _integer(server_ms, 'server time')
    require(isinstance(rows, list) and len(rows) <= count + 1, 'Invalid candle response')
    normalized = []
    previous = None
    for row in rows:
        require(isinstance(row, list) and len(row) >= 7, 'Malformed candle response row')
        opened, ended = (_integer(row[i], 'candle timestamp') for i in (0, 6))
        require(opened % step == 0 and ended == opened + step - 1, 'Unexpected candle timestamps')
        require(opened <= server_ms, 'Candle begins after market server time')
        if previous is not None:
            require(opened - previous == step, 'Raw candles duplicated, out of order, or have gaps')
        previous = opened
        values = [_number(row[i], True, True) for i in range(1, 5)] + [_number(row[5])]
        opening, high, low, close = values[:4]
        require(low <= min(opening, close) <= max(opening, close) <= high, 'Invalid OHLCV values')
        if ended < server_ms:
            normalized.append([opened, *(str(value) for value in values)])
    return checked_candles(normalized, interval, server_ms)


def features(candles15m, candles1h, candles4h):
    """EMA20 uses an initial SMA20; ATR14 uses Wilder smoothing of true range."""
    rows = checked_candles(candles15m, '15m')
    closes = [D(row[4]) for row in rows]
    ema = sum(closes[:20], D(0)) / 20
    previous_ema = None
    alpha = D(2) / 21
    for close in closes[20:]:
        previous_ema = ema
        ema = alpha * close + (1 - alpha) * ema
    true_ranges = [max(D(row[2]) - D(row[3]), abs(D(row[2]) - previous),
                       abs(D(row[3]) - previous)) for row, previous in zip(rows[1:], closes)]
    atr = sum(true_ranges[:14], D(0)) / 14
    for true_range in true_ranges[14:]:
        atr = (atr * 13 + true_range) / 14
    four_closes = [D(row[4]) for row in checked_candles(candles4h, '4h')]
    slow = hourly.features(checked_candles(candles1h, '1h'))
    values = {
        'closedPrice15m': closes[-1], 'previousClose15m': closes[-2],
        'previousLow15m': D(rows[-2][3]), 'previousEma20_15m': previous_ema,
        'ema20_15m': ema, 'atr14_15m': atr,
        'closedPrice4h': four_closes[-1], 'sma20_4h': sum(four_closes[-20:]) / 20,
        'sma50_4h': sum(four_closes[-50:]) / 50,
    }
    values.update({key: slow[key] for key in FEATURE_KEYS if key in slow})
    return {key: str(values[key]) for key in FEATURE_KEYS}


def validate_features(data):
    start, end, server_ms = _check_collection(data, MAX_FEATURE_COLLECTION_SECONDS)
    bar = _integer(data['bar'], 'feature bar')
    require(data['schemaVersion'] == 4 and data['candleColumns'] == COLUMNS, 'Invalid feature schema')
    require(int(start * 1000) // BAR_MS == server_ms // BAR_MS == int(end * 1000) // BAR_MS == bar,
            'Feature collection crossed 15-minute bar')
    for symbol in SYMBOLS:
        quote = data['symbols'][symbol]
        candles = {interval: checked_candles(quote[key], interval, server_ms)
                   for interval, key in CANDLE_KEYS.items()}
        actual = features(candles['15m'], candles['1h'], candles['4h'])
        for key, value in actual.items():
            supplied = D(str(quote[key]))
            require(supplied.is_finite() and supplied == D(value), 'Indicator/candle mismatch: ' + key)
    return data


def compact_features(data):
    return {key: data[key] for key in ('bar', 'serverTimeMs', 'startedAt', 'completedAt')} | {
        'symbols': {symbol: {key: data['symbols'][symbol][key] for key in FEATURE_KEYS} for symbol in SYMBOLS},
    }


def collect_features():
    core = _core()
    start = time.time()
    server_ms = _integer(core.get_json(core.MARKET_URL + '/api/v3/time')['serverTime'], 'server time')

    def fetch(request):
        symbol, interval = request
        step, count = INTERVALS[interval]
        # Request exactly the already closed history at the frozen server time.
        raw = core.get_json(core.MARKET_URL + '/api/v3/klines?' + urlencode({
            'symbol': symbol, 'interval': interval, 'limit': count,
            'endTime': server_ms // step * step - 1,
        }))
        return symbol, interval, closed_candles(raw, interval, server_ms)

    histories = {symbol: {} for symbol in SYMBOLS}
    with ThreadPoolExecutor(max_workers=5) as executor:
        for symbol, interval, rows in executor.map(fetch, [(s, i) for s in SYMBOLS for i in INTERVALS]):
            histories[symbol][interval] = rows
    symbols = {}
    for symbol, candles in histories.items():
        symbols[symbol] = {CANDLE_KEYS[interval]: rows for interval, rows in candles.items()} | features(
            candles['15m'], candles['1h'], candles['4h'])
    data = {'schemaVersion': 4, 'bar': server_ms // BAR_MS, 'serverTimeMs': server_ms,
            'startedAt': start, 'completedAt': time.time(), 'candleColumns': COLUMNS, 'symbols': symbols}
    return validate_features(data)


def validate_book(book):
    start, end, server_ms = _check_collection(book, MAX_BOOK_COLLECTION_SECONDS)
    minute = server_ms // MINUTE_MS
    require(int(start) // 60 == minute == int(end) // 60, 'Book collection crossed minute')
    for symbol in SYMBOLS:
        quote = book['symbols'][symbol]
        bid, ask = (_number(quote[key], True, True) for key in ('bid', 'ask'))
        require(bid <= ask, 'Crossed order book')
        for key in ('bidQty', 'askQty'):
            _number(quote[key], True)
        received = _timestamp(quote['receivedAt'], 'book receive time')
        require(start <= received <= end, 'Book received outside collection')
    return book


def collect_book():
    core = _core()
    start = time.time()
    server_ms = _integer(core.get_json(core.MARKET_URL + '/api/v3/time')['serverTime'], 'server time')

    def fetch(symbol):
        book = core.get_json(core.MARKET_URL + '/api/v3/ticker/bookTicker?' + urlencode({'symbol': symbol}))
        received = time.time()
        require(book['symbol'] == symbol, 'Order book symbol mismatch')
        return symbol, {
            'bid': str(_number(book['bidPrice'], True, True)),
            'ask': str(_number(book['askPrice'], True, True)),
            'bidQty': str(_number(book['bidQty'], True)),
            'askQty': str(_number(book['askQty'], True)), 'receivedAt': received,
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        symbols = dict(executor.map(fetch, SYMBOLS))
    return validate_book({'startedAt': start, 'completedAt': time.time(), 'serverTimeMs': server_ms,
                          'symbols': symbols})


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':'), sort_keys=True).encode()).hexdigest()


def _publish_once(path, value):
    """Create-only durable publication; return False if another publisher won."""
    core = _core()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / ('.' + path.stem + '-' + uuid.uuid4().hex + '.json')
    try:
        core.save_json(temporary, value)
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        core.sync_directory(path.parent)
        return True
    finally:
        if temporary.exists():
            temporary.unlink()


def read_features(root, bar):
    _integer(bar, 'feature bar')
    path = Path(root) / 'features' / (str(bar) + '.json')
    if not path.exists():
        return None
    artifact = json.loads(path.read_text(encoding='utf-8'))
    feature_id = artifact.pop('featureId')
    require(feature_id == digest(artifact), 'Feature checksum mismatch')
    require(artifact['bar'] == bar, 'Feature bar mismatch')
    validate_features(artifact)
    return artifact | {'featureId': feature_id}


def validate_event(event, now=None):
    minute = _integer(event['minute'], 'event minute')
    require(event['schemaVersion'] == 4, 'Invalid minute event schema')
    require(event['featureBar'] == minute // 15, 'Event references a different feature bar')
    for name in ('decision', 'execution'):
        book = validate_book(event[name])
        require(book['serverTimeMs'] // MINUTE_MS == minute, 'Book/event minute mismatch')
    decision, execution = event['decision'], event['execution']
    require(execution['startedAt'] - decision['completedAt'] >= MIN_EXECUTION_DELAY_SECONDS,
            'Execution quote observed before minimum delay')
    require(execution['serverTimeMs'] >= decision['serverTimeMs'], 'Market server clock reversed')
    if now is not None:
        _timestamp(now, 'validation time')
        require(execution['completedAt'] <= now and int(now) // 60 == minute
                and 0 <= now - decision['startedAt'] < MAX_EVENT_AGE_SECONDS,
                'Market event expired or from the future')
    if 'features' in event:
        feature = event['features']
        require(feature['bar'] == event['featureBar'], 'Resolved feature bar mismatch')
        require(feature['completedAt'] <= decision['startedAt'], 'Features completed after decision book')
        require(feature['serverTimeMs'] <= decision['serverTimeMs'], 'Feature server time after decision book')
    return event


def read_event(root, minute, feature_cache=None):
    _integer(minute, 'event minute')
    path = Path(root) / (str(minute) + '.json')
    if not path.exists():
        return None
    event = json.loads(path.read_text(encoding='utf-8'))
    market_id = event.pop('marketId')
    require(market_id == digest(event), 'Minute event checksum mismatch')
    require(event['minute'] == minute, 'Event minute mismatch')
    # Resolve only a numeric bar, never an arbitrary path from the event.
    bar = event['featureBar']
    _integer(bar, 'feature bar')
    cache_key = (str(Path(root).resolve()), bar)
    feature = feature_cache.get(cache_key) if feature_cache is not None else None
    if feature is None:
        feature = read_features(root, bar)
        if feature_cache is not None and feature is not None:
            # A caller-scoped cache validates each immutable artifact once per
            # report, avoiding 15 full history recomputations for the same bar.
            feature_cache[cache_key] = feature
    require(feature is not None, 'Minute event feature artifact missing')
    require(feature['featureId'] == event['featureId'], 'Minute event feature checksum mismatch')
    resolved = event | {'marketId': market_id, 'features': compact_features(feature)}
    return validate_event(resolved)


def publish(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    minute = int(time.time()) // 60
    existing = read_event(root, minute)
    if existing is not None:
        return {'status': 'skipped', 'minute': minute, 'marketId': existing['marketId']}
    bar = minute // 15
    feature = read_features(root, bar)
    if feature is None:
        collected = collect_features()
        require(collected['bar'] == bar, 'Feature collection crossed requested bar')
        _publish_once(root / 'features' / (str(bar) + '.json'), collected | {'featureId': digest(collected)})
        feature = read_features(root, bar)
        require(feature is not None, 'Feature publication did not create artifact')
    decision = collect_book()
    # A monotonic sleep would guarantee elapsed duration even if wall time moves;
    # validation independently rejects a backward or insufficient wall-clock gap.
    time.sleep(MIN_EXECUTION_DELAY_SECONDS)
    execution = collect_book()
    event = {'schemaVersion': 4, 'minute': minute, 'featureBar': bar, 'featureId': feature['featureId'],
             'decision': decision, 'execution': execution}
    validate_event(event | {'features': compact_features(feature)}, time.time())
    require(int(time.time()) // 60 == minute, 'Collection crossed minute; do not publish')
    event['marketId'] = digest(event)
    published = _publish_once(root / (str(minute) + '.json'), event)
    actual = read_event(root, minute)
    require(actual is not None, 'Minute publication did not create event')
    return {'status': 'published' if published else 'skipped', 'minute': minute, 'marketId': actual['marketId']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'paper-intraday-v4' / 'market')
    args = parser.parse_args()
    core = _core()
    os.umask(0o077)
    args.root.mkdir(parents=True, exist_ok=True)
    with core.exclusive_lock(args.root):
        print(core.dumps(publish(args.root)), flush=True)


if __name__ == '__main__':
    main()
