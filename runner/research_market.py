#!/usr/bin/env python3
"""Independent immutable V3 market events with closed-candle slow features.

Public quotes are observed receive times, not exchange execution timestamps.
Order-book telemetry is collected for cost auditing; paper fills remain quotes.
Pure feature/validation functions also work where the Linux runner cannot load.
"""
import argparse
from decimal import Decimal as D
import hashlib
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlencode
import uuid

from research_policy import SYMBOLS

HOUR_MS = 3_600_000
CANDLE_COUNT = 337
COLUMNS = ['openTimeMs', 'open', 'high', 'low', 'close', 'volume']
FEATURES = ('closedPrice', 'sma20', 'sma50', 'sma72', 'sma168',
            'return24hPct', 'return7dPct', 'realizedVolDailyPct')
MAX_COLLECTION_SECONDS = 60
MAX_EVENT_AGE_SECONDS = 240


def _core():
    import run
    return run


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value, positive=False):
    require(not isinstance(value, bool), 'Boolean is not a market number')
    number = D(str(value))
    require(number.is_finite(), 'Market number must be finite')
    require(number > 0 if positive else number >= 0, 'Invalid market price/volume')
    if positive:
        require(number <= D('1000000000'), 'Price exceeds API bounds')
    return number


def _timestamp(value, label):
    require(not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0, 'Invalid ' + label)
    return value


def _checked_candles(candles):
    require(isinstance(candles, list) and len(candles) >= CANDLE_COUNT,
            'Need at least 337 closed hourly candles')
    previous = None
    for row in candles:
        require(isinstance(row, list) and len(row) == 6, 'Malformed normalized candle')
        opened = row[0]
        require(isinstance(opened, int) and not isinstance(opened, bool) and opened >= 0
                and opened % HOUR_MS == 0, 'Invalid hourly candle timestamp')
        if previous is not None:
            require(opened - previous == HOUR_MS, 'Candles duplicated, out of order, or have gaps')
        previous = opened
        opening, high, low, close = (_number(row[i], True) for i in range(1, 5))
        _number(row[5])
        require(low <= min(opening, close) <= max(opening, close) <= high, 'Invalid OHLCV values')
    return candles


def features(candles):
    """Recompute all features; last 336 hourly log returns give daily sample vol."""
    closes = [D(str(row[4])) for row in _checked_candles(candles)[-CANDLE_COUNT:]]
    log_returns = [(b / a).ln() for a, b in zip(closes, closes[1:])]
    mean = sum(log_returns, D(0)) / len(log_returns)
    variance = sum(((r - mean) ** 2 for r in log_returns), D(0)) / (len(log_returns) - 1)
    values = {
        'closedPrice': closes[-1],
        'sma20': sum(closes[-20:]) / 20, 'sma50': sum(closes[-50:]) / 50,
        'sma72': sum(closes[-72:]) / 72, 'sma168': sum(closes[-168:]) / 168,
        'return24hPct': (closes[-1] / closes[-25] - 1) * 100,
        'return7dPct': (closes[-1] / closes[-169] - 1) * 100,
        'realizedVolDailyPct': (variance * 24).sqrt() * 100,
    }
    return {key: str(value) for key, value in values.items()}


def closed_candles(rows, server_ms):
    require(isinstance(rows, list), 'Invalid candle response')
    normalized = []
    previous = None
    for row in rows:
        require(isinstance(row, list) and len(row) >= 7, 'Malformed candle response row')
        opened, ended = row[0], row[6]
        require(isinstance(opened, int) and not isinstance(opened, bool)
                and isinstance(ended, int) and not isinstance(ended, bool)
                and opened % HOUR_MS == 0 and ended == opened + HOUR_MS - 1,
                'Unexpected hourly candle timestamps')
        require(opened <= server_ms, 'Candle begins after market server time')
        if previous is not None:
            require(opened - previous == HOUR_MS, 'Raw candles duplicated, out of order, or have gaps')
        previous = opened
        opening, high, low, close = (_number(row[i], True) for i in range(1, 5))
        volume = _number(row[5])
        require(low <= min(opening, close) <= max(opening, close) <= high, 'Invalid OHLCV values')
        if ended < server_ms:
            normalized.append([opened, str(opening), str(high), str(low), str(close), str(volume)])
    _checked_candles(normalized)
    require(normalized[-1][0] == (server_ms // HOUR_MS - 1) * HOUR_MS,
            'Latest closed candle is stale or looks ahead')
    return normalized[-CANDLE_COUNT:]


def validate_market(market, now=None):
    """Check historical collection integrity; pass now to also enforce freshness."""
    require(isinstance(market, dict), 'Invalid market object')
    start = _timestamp(market['startedAt'], 'collection start')
    end = _timestamp(market['completedAt'], 'collection completion')
    server_ms = market['serverTimeMs']
    require(isinstance(server_ms, int) and not isinstance(server_ms, bool) and server_ms >= 0,
            'Invalid market server time')
    require(0 <= end - start < MAX_COLLECTION_SECONDS, 'Market collection took too long or reversed')
    require(abs(server_ms / 1000 - start) <= 30, 'Market and collector clocks differ by over 30s')
    slot = server_ms // HOUR_MS
    require(int(start) // 3600 == slot == int(end) // 3600, 'Market collection crossed hour')
    if now is not None:
        _timestamp(now, 'validation time')
        require(end <= now and 0 <= now - start < MAX_EVENT_AGE_SECONDS, 'Market event expired or from the future')
    require(set(market['symbols']) == set(SYMBOLS), 'V3 requires fixed five-symbol universe')
    for symbol in SYMBOLS:
        quote = market['symbols'][symbol]
        price = _number(quote['price'], True)
        require(price == price.quantize(D('0.000001')), 'Quote exceeds API decimal precision')
        for key in ('quoteReceivedAt', 'bookReceivedAt'):
            received = _timestamp(quote[key], key)
            require(start <= received <= end, 'Quote/book received outside market collection')
        bid, ask = (_number(quote[key], True) for key in ('bookBidPrice', 'bookAskPrice'))
        require(bid <= ask, 'Crossed order book')
        require(quote['candleColumns'] == COLUMNS, 'Unexpected candle column schema')
        candles = quote['closedHourlyCandles']
        actual = features(candles)
        require(candles[-1][0] == (slot - 1) * HOUR_MS, 'Latest closed candle is stale or looks ahead')
        for key, value in actual.items():
            supplied = D(str(quote[key]))
            require(supplied.is_finite() and supplied == D(value), 'Indicator/candle mismatch: ' + key)
    return market


def collect_market(symbols=SYMBOLS):
    require(tuple(symbols) == SYMBOLS, 'V3 collection requires its fixed five-symbol universe')
    core = _core()
    start = time.time()
    server_ms = core.get_json(core.MARKET_URL + '/api/v3/time')['serverTime']
    require(isinstance(server_ms, int) and not isinstance(server_ms, bool), 'Invalid Binance clock')
    require(abs(server_ms / 1000 - time.time()) <= 30, 'VPS and market clocks differ by over 30s')
    quotes = {}
    for symbol in SYMBOLS:
        candles = closed_candles(core.get_json(core.MARKET_URL + '/api/v3/klines?' + urlencode({
            'symbol': symbol, 'interval': '1h', 'limit': CANDLE_COUNT + 1, 'endTime': server_ms,
        })), server_ms)
        ticker = core.get_json(core.MARKET_URL + '/api/v3/ticker/price?' + urlencode({'symbol': symbol}))
        received = time.time()
        require(ticker['symbol'] == symbol, 'Quote symbol mismatch')
        book = core.get_json(core.MARKET_URL + '/api/v3/ticker/bookTicker?' + urlencode({'symbol': symbol}))
        book_received = time.time()
        require(book['symbol'] == symbol, 'Order book symbol mismatch')
        quotes[symbol] = {
            'price': core.api_decimal(ticker['price']), 'quoteReceivedAt': received,
            'bookBidPrice': str(_number(book['bidPrice'], True)),
            'bookAskPrice': str(_number(book['askPrice'], True)), 'bookReceivedAt': book_received,
            'candleColumns': list(COLUMNS), 'closedHourlyCandles': candles, **features(candles),
        }
    market = {'startedAt': start, 'completedAt': time.time(), 'serverTimeMs': server_ms, 'symbols': quotes}
    validate_market(market, now=time.time())
    return market


def digest(market):
    return hashlib.sha256(json.dumps(market, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def read_event(directory, slot):
    path = Path(directory) / (str(slot) + '.json')
    if not path.exists():
        return None
    event = json.loads(path.read_text(encoding='utf-8'))
    require(event['schemaVersion'] == 3 and event['slot'] == slot, 'Market event slot/schema mismatch')
    market = event['market']
    require(event['marketId'] == digest(market), 'Market event checksum mismatch')
    require(market['serverTimeMs'] // HOUR_MS == slot, 'Market candle hour mismatch')
    validate_market(market)
    return event


def publish(directory):
    core = _core()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    slot = int(time.time()) // 3600
    existing = read_event(directory, slot)
    if existing:
        return {'status': 'skipped', 'marketId': existing['marketId'], 'slot': slot}
    market = collect_market()
    validate_market(market, now=time.time())
    require(int(time.time()) // 3600 == slot == market['serverTimeMs'] // HOUR_MS,
            'Collection crossed hour; do not publish')
    event = {'schemaVersion': 3, 'slot': slot, 'marketId': digest(market), 'market': market}
    path = directory / (str(slot) + '.json')
    temporary = directory / ('.' + str(slot) + '-' + uuid.uuid4().hex + '.json')
    try:
        core.save_json(temporary, event)
        try:
            os.link(temporary, path)  # Atomic create-only publication: never replaces an existing event.
        except FileExistsError:
            existing = read_event(directory, slot)
            require(existing is not None, 'Concurrent market publisher lost event')
            return {'status': 'skipped', 'slot': slot, 'marketId': existing['marketId']}
        core.sync_directory(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {'status': 'published', 'slot': slot, 'marketId': event['marketId']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path.home() / 'paper-research-v3' / 'market')
    args = parser.parse_args()
    core = _core()
    os.umask(0o077)
    args.directory.mkdir(parents=True, exist_ok=True)
    with core.exclusive_lock(args.directory):
        print(core.dumps(publish(args.directory)), flush=True)


if __name__ == '__main__':
    main()
