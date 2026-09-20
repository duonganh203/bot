#!/usr/bin/env python3
"""Disposable V3 real HTTP/SQLite verification with synthetic public-market data.

The two HTTP servers use new temporary databases. No exchange or AI is called.
This test requires the built Linux backend and never uses deployed state paths.
"""
import argparse
from copy import deepcopy
from decimal import Decimal as D
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from unittest.mock import patch

import entry_filter
import research_market as feed
import research_policy as policy
import research_report as report_module
import research_runner as runner
from reconcile import reconcile
from shadow_smoke import free_port


def fixture(now):
    """337 consecutive fully closed hours, independently computed features."""
    slot = int(now) // 3600
    market = {'startedAt': now, 'completedAt': now,
              'serverTimeMs': int(now * 1000), 'symbols': {}}
    for symbol in policy.SYMBOLS:
        rows = [[(slot - 337 + index) * 3600000,
                 str(100 + index), str(101 + index), str(99 + index),
                 str(100 + index), '10'] for index in range(337)]
        market['symbols'][symbol] = {
            'price': '500', 'quoteReceivedAt': now,
            'bookBidPrice': '499.99', 'bookAskPrice': '500.01',
            'bookReceivedAt': now,
            'candleColumns': ['openTimeMs', 'open', 'high', 'low', 'close', 'volume'],
            'closedHourlyCandles': rows,
            **feed.features(rows),
        }
    return market


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=runner.ROOT)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    output = (args.output_dir or project / 'data').resolve()
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='research-smoke-', dir=output))
    root = directory / 'experiment'
    root.mkdir()
    processes, logs, ports, checks = {}, [], {}, []
    for mode in runner.MODES:
        port = free_port()
        while port in ports.values():
            port = free_port()
        ports[mode] = port
    urls = {mode: 'http://127.0.0.1:' + str(port) for mode, port in ports.items()}

    def start(mode):
        log = (directory / (mode + '.log')).open('a')
        logs.append(log)
        environment = {
            **os.environ, 'HOST': '127.0.0.1', 'PORT': str(ports[mode]),
            'API_TOKEN': '', 'RISK_POLICY': 'reduce-only-v2', 'LOG_LEVEL': 'error',
            'DATABASE_PATH': str(directory / (mode + '.sqlite')),
        }
        processes[mode] = subprocess.Popen(
            ['node', 'dist/app/server.js'], cwd=project, env=environment,
            stdout=log, stderr=log)
        for _ in range(100):
            if processes[mode].poll() is not None:
                raise RuntimeError('Backend failed; inspect ' + str(directory / (mode + '.log')))
            try:
                if runner.core.get_json(urls[mode] + '/health')['status'] == 'ok':
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError('Backend startup timed out: ' + mode)

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
        for mode in runner.MODES:
            start(mode)
        now = time.time()
        slot = int(now) // 3600
        market = fixture(now)
        with patch.object(feed, 'collect_market', return_value=market):
            experiment = runner.initialize(root, urls)
        assert experiment['startSlot'] == slot + 1
        assert runner.execute(root, 'baseline')['status'] == 'waiting'
        assert runner.execute(root, 'candidate')['status'] == 'waiting'
        checks.append('Fresh isolated $50 accounts begin at the next hourly slot')

        alias = directory / 'alias-experiment'
        alias.mkdir()
        with patch.object(feed, 'collect_market', return_value=market):
            try:
                runner.initialize(alias, {
                    'baseline': urls['baseline'],
                    'candidate': urls['baseline'].replace('127.0.0.1', 'localhost'),
                })
                raise AssertionError('Two aliases of one database were accepted')
            except ValueError as error:
                assert 'isolation' in str(error).lower(), str(error)
        checks.append('Two hostname aliases of the same database fail isolation checks')

        # Only the disposable fixture begins now; production always begins next hour.
        experiment['startSlot'] = slot
        experiment['createdAt'] = now - 3600
        runner.core.save_json(root / 'experiment.json', experiment)
        (root / 'market').mkdir(exist_ok=True)
        with patch.object(feed, 'collect_market', return_value=market):
            published = feed.publish(root / 'market')
            repeated = feed.publish(root / 'market')
        assert published['status'] == 'published'
        assert repeated['status'] == 'skipped'
        assert repeated['marketId'] == published['marketId']
        event = feed.read_event(root / 'market', slot)
        assert event['schemaVersion'] == 3
        assert all(len(q['closedHourlyCandles']) == 337 for q in event['market']['symbols'].values())
        checks.append('Schema 3 event contains 337 closed hours and publish is immutable')

        # The synthetic fixture explicitly opens the candidate entry window. Its
        # real six-hour cadence is independently exercised in policy unit tests.
        with patch.object(policy, 'entry_window', return_value=True), \
                patch.object(entry_filter, 'vote', side_effect=AssertionError('Unexpected AI vote')), \
                patch.object(runner.core, 'analyze', side_effect=AssertionError('Unexpected AI analysis')):
            dry = runner.execute(root, 'candidate', dry_run=True)
            assert dry['payload']['action'] == 'BUY'
            assert not runner.core.get_json(urls['candidate'] + '/api/trades')['trades']
            assert not (root / 'candidate' / 'state.json').exists()
            candidate = runner.execute(root, 'candidate')
            baseline = runner.execute(root, 'baseline')
        assert candidate['response']['status'] == 'executed'
        assert baseline['response']['status'] == 'executed'
        assert candidate['response']['trade']['symbol'] == 'BTCUSDT'
        saved = runner.core.read_json(root / 'candidate' / 'state.json')
        candidate_evidence = runner.core.read_json(Path(saved['runDir']) / 'evidence.json')
        baseline_state = runner.core.read_json(root / 'baseline' / 'state.json')
        assert saved['marketId'] == baseline_state['marketId'] == published['marketId']
        assert candidate_evidence.get('aiVote') is None
        checks.append('Both strategies consume one market event; dry-run has no fill and no AI is called')

        stop('candidate')
        start('candidate')
        # Simulate a process crash after server commit, before the client recorded
        # the known result. Recovery must use exactly the stored body and key.
        pending = {**saved, 'complete': False}
        pending.pop('result', None)
        runner.core.save_json(root / 'candidate' / 'state.json', pending)
        with patch.object(runner, 'version', return_value='v3-synthetic-source-change'):
            replay = runner.execute(root, 'candidate')
        assert replay['response']['decisionId'] == candidate['response']['decisionId']
        assert replay['replayed'] is True
        recovered = runner.core.read_json(root / 'candidate' / 'state.json')
        assert recovered['body'] == saved['body'] and recovered['key'] == saved['key']
        assert runner.execute(root, 'candidate')['status'] == 'skipped'
        assert len(runner.core.get_json(urls['candidate'] + '/api/trades')['trades']) == 1
        checks.append('Restart and unresolved-outcome recovery preserve one fill and the exact saved request')

        with patch.object(runner, 'version', return_value='v3-synthetic-source-change'):
            try:
                runner.execute(root, 'candidate')
                raise AssertionError('A changed source pin was accepted for a fresh decision')
            except ValueError as error:
                assert 'changed' in str(error).lower() or 'version' in str(error).lower(), str(error)
        checks.append('A source version change blocks fresh work while exact pending recovery remains possible')

        report = report_module.summarize(root, market=market)
        assert report['matchedMarketSlots'] == 1
        assert report['accounts']['baseline']['aiVotes'] == {}
        assert report['accounts']['candidate']['aiVotes'] == {}
        assert report['promotion']['eligible'] is False
        ledgers = {mode: reconcile(directory / (mode + '.sqlite')) for mode in runner.MODES}
        for mode in runner.MODES:
            assert ledgers[mode]['trades'] == 1
            assert D(ledgers[mode]['fees']) > 0
            assert D(str(report['accounts'][mode]['feeReconciliationDifferenceUsd'])) == 0
        checks.append('Shared-event reporting, costs and both complete SQLite ledgers reconcile')

        # Use a real backend SELL to distinguish an executed full exit from a
        # proposal, rejection or partial exit. This explicit fixture POST is kept
        # outside the two runner histories and occurs after their paired report.
        before_sell = runner.checked_context(urls['candidate'], market)
        sold = policy.hold('Disposable smoke: complete the synthetic position')
        sold.update(action='SELL', symbol='BTCUSDT', amountUsd=dry['payload']['amountUsd'])
        sell_payload = runner.payload_for(sold, before_sell, market, 'candidate', experiment['version'])
        sell_body = runner.core.dumps(sell_payload)
        status, _, sell_result = runner.core.http(
            'POST', urls['candidate'] + '/api/signals', sell_body, str(uuid.uuid4()))
        assert status == 200 and sell_result['status'] == 'executed'
        after_sell = runner.checked_context(urls['candidate'], market)
        successful_exit = {
            'slot': slot, 'body': sell_body, 'policyState': {'lastExitSlot': {}},
            'result': {'response': sell_result},
        }
        cooldown = runner.policy_state(successful_exit, after_sell, market)
        assert cooldown == {'lastExitSlot': {'BTCUSDT': slot}}
        assert runner.policy_state({**successful_exit, 'policyState': cooldown}, after_sell, market) == cooldown
        assert runner.policy_state(successful_exit, before_sell, market) == {'lastExitSlot': {}}
        for outcome in ('held', 'rejected'):
            not_filled = deepcopy(successful_exit)
            not_filled['result']['response']['status'] = outcome
            assert runner.policy_state(not_filled, after_sell, market) == {'lastExitSlot': {}}
        with patch.object(policy, 'entry_window', return_value=True):
            next_candidate, _, gates, _ = policy.plan(after_sell, market, (), cooldown)
        assert next_candidate.get('symbol') != 'BTCUSDT'
        assert gates['BTCUSDT']['cooldownRemainingHours'] == 24
        checks.append('Only an executed full SELL starts persistent cooldown; partial/rejected/held outcomes do not')
        final_ledgers = {mode: reconcile(directory / (mode + '.sqlite')) for mode in runner.MODES}
        assert final_ledgers['candidate']['trades'] == 2
        assert final_ledgers['candidate']['completedRoundTrips'] == 1
        assert final_ledgers['baseline']['trades'] == 1
        runner.core.save_json(directory / 'verification.json', {
            'status': 'passed', 'checks': checks,
            'syntheticOverrides': ['Only this temporary experiment starts in the current slot',
                                   'Candidate entry_window is opened for HTTP fixture execution'],
            'reportBeforeExplicitExitFixture': report, 'ledgersBeforeExplicitExitFixture': ledgers,
            'successfulExitFixture': sell_result, 'cooldown': cooldown, 'finalLedgers': final_ledgers,
        })
        print(runner.core.dumps({'status': 'passed', 'directory': str(directory), 'checks': checks}))
    finally:
        for mode in processes:
            stop(mode)
        for log in logs:
            log.close()


if __name__ == '__main__':
    main()
