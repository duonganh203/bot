#!/usr/bin/env python3
"""Real HTTP + SQLite control verification on two temporary backend processes."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
from unittest.mock import patch
import uuid

import shadow
import shadow_report


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    output = args.output_dir or project / 'data'
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='shadow-smoke-', dir=output)).resolve()
    processes, handles = {}, []
    ports = {'ai': free_port(), 'control': free_port()}
    while ports['control'] == ports['ai']:
        ports['control'] = free_port()
    urls = {k: 'http://127.0.0.1:' + str(v) for k, v in ports.items()}

    def start(name):
        log = (directory / (name + '.log')).open('a')
        handles.append(log)
        env = {**os.environ, 'HOST': '127.0.0.1', 'PORT': str(ports[name]), 'API_TOKEN': '',
               'LOG_LEVEL': 'error', 'DATABASE_PATH': str(directory / (name + '.sqlite'))}
        processes[name] = subprocess.Popen(['node', 'dist/app/server.js'], cwd=project, env=env,
                                          stdout=log, stderr=log)
        for _ in range(100):
            if processes[name].poll() is not None:
                raise RuntimeError('Temporary backend failed: ' + name)
            try:
                if shadow.core.get_json(urls[name] + '/health')['status'] == 'ok':
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError('Temporary backend startup timed out')

    def stop(name):
        p = processes.get(name)
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)

    checks = []
    try:
        start('ai')
        start('control')
        now = time.time()
        slot = int(now) // 3600
        market = {'startedAt': now, 'serverTimeMs': int(now * 1000), 'symbols': {}}
        for symbol in shadow.core.SYMBOLS:
            rows = [[(slot - 100 + i) * 3600000, str(100 + i), str(101 + i), str(99 + i),
                     str(100 + i), '10'] for i in range(100)]
            closes = [D(row[4]) for row in rows]
            market['symbols'][symbol] = {'price': '200' if symbol == 'BTCUSDT' else '100',
                'closedHourlyCandles': rows, 'sma20': str(sum(closes[-20:]) / 20),
                'sma50': str(sum(closes[-50:]) / 50),
                'return24hPct': str((closes[-1] / closes[-25] - 1) * 100)}
        source_dir, state_dir = directory / 'ai-state', directory / 'control-state'
        source_run = source_dir / 'runs' / 'synthetic'
        source_run.mkdir(parents=True)
        state_dir.mkdir()
        context = shadow.core.fetch_context(urls['ai'], market)
        hold = {'action': 'HOLD', 'symbol': None, 'amountUsd': None, 'confidence': 0.8,
                'riskLevel': 'LOW', 'rationale': 'Synthetic AI HOLD for isolated smoke verification.'}
        body = shadow.core.make_payload(hold, context, market, 'v1-smoke')
        status, _, response = shadow.core.http('POST', urls['ai'] + '/api/signals', shadow.core.dumps(body), str(uuid.uuid4()))
        assert status == 200 and response['status'] == 'held'
        source = {'market': market, 'context': context}
        shadow.core.save_json(source_run / 'snapshot.json', source)
        source_state = {'complete': True, 'backend': urls['ai'], 'slot': slot, 'slotSeconds': 3600,
            'runDir': str(source_run), 'body': shadow.core.dumps(body),
            'result': {'httpStatus': status, 'response': response, 'replayed': False}}
        shadow.core.save_json(source_dir / 'state.json', source_state)
        # Current-slot start is only for this explicitly synthetic disposable smoke.
        experiment = {'schemaVersion': 1, 'createdAt': now, 'startSlot': slot, 'backend': urls['control'],
            'baseline': urls['ai'], 'sourceDir': str(source_dir), 'strategyVersion': shadow.version(),
            'aiStrategyVersion': 'v1-smoke', 'policy': shadow.POLICY}
        shadow.core.save_json(state_dir / 'experiment.json', experiment)
        dry = shadow.execute(state_dir, source_dir, urls['control'], urls['ai'], True)
        assert dry['payload']['action'] == 'BUY' and not (state_dir / 'state.json').exists()
        assert not shadow.core.get_json(urls['control'] + '/api/trades')['trades']
        checks.append('Dry run selects BUY but leaves control portfolio and slot unchanged')
        result = shadow.execute(state_dir, source_dir, urls['control'], urls['ai'])
        assert result['response']['status'] == 'executed'
        assert result['response']['trade']['grossUsd'] == 5
        assert not shadow.core.get_json(urls['ai'] + '/api/trades')['trades']
        checks.append('Control executes one $5 BUY while separate AI account remains cash')
        saved = shadow.core.read_json(state_dir / 'state.json')
        stop('control')
        start('control')
        replay_status, headers, replay = shadow.core.http('POST', urls['control'] + '/api/signals', saved['body'], saved['key'])
        assert replay_status == 200 and replay['decisionId'] == result['response']['decisionId']
        assert any(k.lower() == 'idempotency-replayed' and v == 'true' for k, v in headers.items())
        assert shadow.execute(state_dir, source_dir, urls['control'], urls['ai'])['status'] == 'skipped'
        assert len(shadow.core.get_json(urls['control'] + '/api/trades')['trades']) == 1
        checks.append('Restart and exact HTTP retry retain one decision/fill; hourly rerun skips')
        try:
            shadow.separate_context(urls['ai'].replace('127.0.0.1', 'localhost'), urls['ai'], source, market)
            raise AssertionError('Aliased primary backend accepted')
        except ValueError as error:
            assert 'isolation' in str(error)
        checks.append('A different hostname pointing at the same account is rejected before orders')
        with patch.object(shadow.core, 'collect_market', return_value=market):
            report = shadow_report.summarize(state_dir)
        assert report['controlBuyOpportunityAgreement'] == {'ai_held': 1}
        assert report['accounts']['control']['portfolio']['equity'] == 49.995
        assert report['accounts']['ai']['portfolio']['equity'] == 50
        assert D(report['controlObservedMaxDrawdownPct']) == D('0.01')
        checks.append('Comparison report records AI HOLD, control BUY, net equity and fee drawdown')
        for name in ('ai', 'control'):
            with sqlite3.connect('file:' + str(directory / (name + '.sqlite')) + '?mode=ro', uri=True) as db:
                assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
                assert not db.execute('pragma foreign_key_check').fetchall()
                cash, fees = db.execute('select cash, total_fees from portfolio').fetchone()
                assert D(cash) == (D('50') if name == 'ai' else D('44.995'))
                assert D(fees) == (D('0') if name == 'ai' else D('0.005'))
        checks.append('Both real SQLite ledgers reconcile; integrity and foreign keys pass')
        verification = {'status': 'passed', 'directory': str(directory), 'checks': checks, 'report': report}
        shadow.core.save_json(directory / 'verification.json', verification)
        print(json.dumps({'status': 'passed', 'directory': str(directory), 'checks': checks}, indent=2))
    finally:
        for name in processes:
            stop(name)
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
