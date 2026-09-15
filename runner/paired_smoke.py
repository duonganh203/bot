#!/usr/bin/env python3
"""Disposable real HTTP/SQLite verification; synthetic market, no model call."""
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
import market_snapshot as feed
from reconcile import reconcile
from shadow_smoke import free_port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=paired.ROOT)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    output = args.output_dir or project / 'data'
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='paired-smoke-', dir=output))
    root = directory / 'experiment'
    root.mkdir()
    processes, handles, checks = {}, [], []
    ports = {'ai': free_port(), 'control': free_port()}
    while ports['ai'] == ports['control']:
        ports['control'] = free_port()
    urls = {k: 'http://127.0.0.1:' + str(p) for k, p in ports.items()}

    def start(mode):
        log = (directory / (mode + '.log')).open('a')
        handles.append(log)
        env = {**os.environ, 'HOST': '127.0.0.1', 'PORT': str(ports[mode]), 'API_TOKEN': '',
               'RISK_POLICY': 'reduce-only-v2', 'LOG_LEVEL': 'error',
               'DATABASE_PATH': str(directory / (mode + '.sqlite'))}
        processes[mode] = subprocess.Popen(['node', 'dist/app/server.js'], cwd=project, env=env, stdout=log, stderr=log)
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
        for mode in paired.MODES:
            start(mode)
        now = time.time()
        slot = int(now) // 3600
        market = {'startedAt': now, 'serverTimeMs': int(now * 1000), 'symbols': {}}
        for symbol in paired.core.SYMBOLS:
            rows = [[(slot - 100 + i) * 3600000, str(100 + i), str(101 + i), str(99 + i), str(100 + i), '10'] for i in range(100)]
            closes = [D(r[4]) for r in rows]
            market['symbols'][symbol] = {'price': '200' if symbol == 'BTCUSDT' else '100', 'quoteReceivedAt': now,
                'sma20': str(sum(closes[-20:]) / 20), 'sma50': str(sum(closes[-50:]) / 50),
                'return24hPct': str((closes[-1] / closes[-25] - 1) * 100), 'closedHourlyCandles': rows}
        with patch.object(paired.core, 'collect_market', return_value=market):
            experiment = paired.initialize(root, urls)
        assert experiment['startSlot'] == slot + 1
        assert paired.execute(root, 'control')['status'] == 'waiting'
        checks.append('Fresh isolated $50 accounts initialize forward-only at the next slot')
        # Only this explicitly disposable synthetic experiment starts in the current slot.
        experiment['startSlot'] = slot
        paired.core.save_json(root / 'experiment.json', experiment)
        (root / 'market').mkdir()
        with patch.object(feed.core, 'collect_market', return_value=market):
            event = feed.publish(root / 'market')
        assert event['status'] == 'published'
        with patch.object(paired.entry_filter, 'vote', side_effect=AssertionError('Control called AI')):
            dry = paired.execute(root, 'control', True)
            assert dry['payload']['action'] == 'BUY'
            assert not paired.core.get_json(urls['control'] + '/api/trades')['trades']
            result = paired.execute(root, 'control')
        assert result['response']['status'] == 'executed'
        assert not (root / 'ai' / 'state.json').exists()
        checks.append('Control buys $5 from independent event before any AI run; dry run does not trade')
        with patch.object(paired.entry_filter, 'vote', side_effect=RuntimeError('Synthetic AI outage')):
            ai = paired.execute(root, 'ai')
        assert ai['response']['status'] == 'held'
        checks.append('AI outage is audited as an unavailable entry vote; control is unaffected')
        saved = paired.core.read_json(root / 'control' / 'state.json')
        stop('control')
        start('control')
        status, headers, replay = paired.core.http('POST', urls['control'] + '/api/signals', saved['body'], saved['key'])
        assert status == 200 and replay['decisionId'] == result['response']['decisionId']
        assert any(k.lower() == 'idempotency-replayed' and v == 'true' for k, v in headers.items())
        assert paired.execute(root, 'control')['status'] == 'skipped'
        assert len(paired.core.get_json(urls['control'] + '/api/trades')['trades']) == 1
        checks.append('Restart plus exact HTTP retry leaves one fill; same hourly slot is skipped')
        alias = directory / 'alias-experiment'
        alias.mkdir()
        with patch.object(paired.core, 'collect_market', return_value=market):
            try:
                paired.initialize(alias, {'ai': urls['ai'], 'control': urls['ai'].replace('127.0.0.1', 'localhost')})
                raise AssertionError('Alias accepted')
            except ValueError as error:
                assert 'isolation' in str(error)
        checks.append('Two hostnames pointing at the same database cannot initialize an experiment')
        with patch.object(paired_report.core, 'collect_market', return_value=market):
            report = paired_report.summarize(root)
        assert report['matchedMarketSlots'] == 1 and report['sameRuleProposalSlots'] == 1
        assert report['accounts']['ai']['aiVotes'] == {'error': 1}
        assert report['accounts']['control']['portfolio']['equity'] == 49.995
        assert report['accounts']['ai']['portfolio']['equity'] == 50
        assert D(report['accounts']['control']['observedMaxDrawdownPct']) == D('0.01')
        ledgers = {mode: reconcile(directory / (mode + '.sqlite')) for mode in paired.MODES}
        assert ledgers['control']['trades'] == 1 and ledgers['ai']['trades'] == 0
        checks.append('Report and both complete ledgers reconcile cash, inventory, fees, versions and decision links')
        paired.core.save_json(directory / 'verification.json', {'status': 'passed', 'checks': checks, 'report': report, 'ledgers': ledgers})
        print(paired.core.dumps({'status': 'passed', 'directory': str(directory), 'checks': checks}))
    finally:
        for mode in processes:
            stop(mode)
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
