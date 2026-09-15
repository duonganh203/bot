#!/usr/bin/env python3
"""Independent immutable market event producer. Never reads an AI run."""
import argparse
import hashlib
from pathlib import Path
import time

import run as core
from shadow import validate_market


def digest(market):
    return hashlib.sha256(core.dumps(market).encode()).hexdigest()


def read_event(directory, slot):
    path = directory / (str(slot) + '.json')
    if not path.exists():
        return None
    event = core.read_json(path)
    core.require(event['schemaVersion'] == 2 and event['slot'] == slot, 'Market event slot/schema mismatch')
    market = event['market']
    core.require(event['marketId'] == digest(market), 'Market event checksum mismatch')
    core.require(market['serverTimeMs'] // core.HOUR_MS == slot, 'Market candle hour mismatch')
    core.require(int(market['startedAt']) // 3600 == slot, 'Market collection crossed hour')
    validate_market(market)
    return event


def publish(directory):
    slot = int(time.time()) // 3600
    existing = read_event(directory, slot)
    if existing:
        return {'status': 'skipped', 'marketId': existing['marketId'], 'slot': slot}
    market = core.collect_market()
    validate_market(market)
    core.require(int(time.time()) // 3600 == slot == market['serverTimeMs'] // core.HOUR_MS,
                 'Collection crossed hour; do not publish')
    core.require(time.time() - market['startedAt'] < 60, 'Market collection took too long')
    event = {'schemaVersion': 2, 'slot': slot, 'marketId': digest(market), 'market': market}
    core.save_json(directory / (str(slot) + '.json'), event)
    return {'status': 'published', 'slot': slot, 'marketId': event['marketId']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path.home() / 'paper-v2' / 'market')
    args = parser.parse_args()
    core.os.umask(0o077)
    args.directory.mkdir(parents=True, exist_ok=True)
    with core.exclusive_lock(args.directory):
        print(core.dumps(publish(args.directory)), flush=True)


if __name__ == '__main__':
    main()
