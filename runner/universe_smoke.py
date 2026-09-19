#!/usr/bin/env python3
"""Disposable two/five-universe HTTP, retry, ledger and reporting verification."""
import argparse
from decimal import Decimal as D
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

import paired
import paired_report
import policy
import market_snapshot as feed
from reconcile import reconcile
from shadow_smoke import free_port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=paired.ROOT / 'data')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='universe-smoke-', dir=args.output_dir))
    root = directory / 'experiment'
    root.mkdir()
    processes, logs, ports = {}, [], {}
    for mode in policy.UNIVERSES:
        port = free_port()
        while port in ports.values():
            port = free_port()
        ports[mode] = port
    urls = {m: 'http://127.0.0.1:' + str(p) for m, p in ports.items()}

    def start(mode):
        log = (directory / (mode + '.log')).open('a')
        logs.append(log)
        env = {**os.environ, 'HOST': '127.0.0.1', 'PORT': str(ports[mode]), 'API_TOKEN': '',
               'RISK_POLICY': 'reduce-only-v2', 'LOG_LEVEL': 'error',
               'DATABASE_PATH': str(directory / (mode + '.sqlite'))}
        processes[mode] = subprocess.Popen(['node', 'dist/app/server.js'], cwd=paired.ROOT,
                                           env=env, stdout=log, stderr=log)
        for _ in range(100):
            if processes[mode].poll() is not None:
                raise RuntimeError('Backend failed: ' + mode)
            try:
                if paired.core.get_json(urls[mode] + '/health')['status'] == 'ok':
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError('Backend startup timed out')

    def stop(mode):
        process = processes.get(mode)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    try:
        for mode in policy.UNIVERSES:
            start(mode)
        now = time.time()
        slot = int(now) // 3600
        market = {'startedAt': now, 'serverTimeMs': int(now * 1000), 'symbols': {}}
        for symbol in policy.UNIVERSES['five']:
            rows = [[(slot - 100 + i) * 3600000, str(100 + i), str(101 + i), str(99 + i), str(100 + i), '10'] for i in range(100)]
            closes = [D(row[4]) for row in rows]
            market['symbols'][symbol] = {'price': '100' if symbol in policy.SYMBOLS else '200',
                'quoteReceivedAt': now, 'sma20': str(sum(closes[-20:]) / 20),
                'sma50': str(sum(closes[-50:]) / 50),
                'return24hPct': str((closes[-1] / closes[-25] - 1) * 100), 'closedHourlyCandles': rows}
        with patch.object(paired.core, 'collect_market', return_value=market):
            experiment = paired.initialize(root, urls, policy.UNIVERSES)
        assert experiment['startSlot'] == slot + 1
        assert paired.execute(root, 'five')['status'] == 'waiting'
        # This synthetic disposable experiment alone runs its test in this slot.
        experiment['startSlot'] = slot
        paired.core.save_json(root / 'experiment.json', experiment)
        (root / 'market').mkdir()
        with patch.object(feed.core, 'collect_market', return_value=market):
            feed.publish(root / 'market', policy.UNIVERSES['five'])
        with patch.object(paired.entry_filter, 'vote', side_effect=AssertionError('AI called')):
            dry = paired.execute(root, 'five', True)
            assert dry['payload']['symbol'] == 'SOLUSDT'
            assert not paired.core.get_json(urls['five'] + '/api/trades')['trades']
            two = paired.execute(root, 'two')
            five = paired.execute(root, 'five')
        assert two['response']['status'] == 'held'
        assert five['response']['status'] == 'executed'
        assert five['response']['trade']['symbol'] == 'SOLUSDT'
        saved = paired.core.read_json(root / 'five' / 'state.json')
        stop('five')
        start('five')
        status, _, replay = paired.core.http('POST', urls['five'] + '/api/signals', saved['body'], saved['key'])
        assert status == 200 and replay['decisionId'] == five['response']['decisionId']
        assert paired.execute(root, 'five')['status'] == 'skipped'
        with patch.object(paired_report.core, 'collect_market', return_value=market):
            report = paired_report.summarize(root)
        assert report['matchedMarketSlots'] == 1 and report['sameRuleProposalSlots'] == 0
        assert report['accounts']['five']['buysBySymbol'] == {'SOLUSDT': 1}
        assert report['accounts']['five']['portfolio']['equity'] == 49.995
        assert report['accounts']['five']['extraExecutionCostEstimateUsd']['5'] == '0.0025'
        assert report['accounts']['two']['portfolio']['equity'] == 50
        ledgers = {mode: reconcile(directory / (mode + '.sqlite')) for mode in policy.UNIVERSES}
        assert ledgers['five']['trades'] == 1 and ledgers['two']['trades'] == 0
        checks = ['Fresh independent $50 accounts and common start slot',
                  'Identical five-coin snapshot: two-coin HOLD versus five-coin SOL BUY, no AI',
                  'Dry-run sends no order; restart and exact retry preserve one SOL fill',
                  'Shared-event reporting, fees, cost estimates and both full ledgers reconcile']
        paired.core.save_json(directory / 'verification.json', {'status': 'passed', 'checks': checks, 'report': report, 'ledgers': ledgers})
        print(paired.core.dumps({'status': 'passed', 'directory': str(directory), 'checks': checks}))
    finally:
        for mode in processes:
            stop(mode)
        for log in logs:
            log.close()


if __name__ == '__main__':
    main()
