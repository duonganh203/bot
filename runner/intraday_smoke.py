#!/usr/bin/env python3
"""Disposable V4 HTTP/SQLite restart, exact-retry and sequential-exit smoke.

Uses two temporary databases and synthetic policy fixtures. No public market,
exchange account, AI service or existing deployed state is touched. Market
publication/no-lookahead integrity is covered by test_intraday_market instead.
"""
import argparse
from copy import deepcopy
from decimal import Decimal as D
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

import intraday_market as feed
import intraday_report as reporting
import intraday_runner as runner
from reconcile import reconcile
from shadow_smoke import free_port
from test_intraday_policy import fixture as policy_fixture


def fixture(now, bid='100', ask='100.1'):
    _, features, book, _ = policy_fixture()
    minute = int(now) // 60
    features.update(bar=minute // 15, serverTimeMs=int((now - 5) * 1000),
                    startedAt=now - 5, completedAt=now - 4)
    books = []
    for offset in (3, 0):
        current = deepcopy(book)
        current.update(startedAt=now - offset - 0.5, completedAt=now - offset,
                       serverTimeMs=int((now - offset - 0.5) * 1000))
        for quote in current['symbols'].values():
            quote.update(bid=bid, ask=ask, bidQty='100', askQty='100', receivedAt=now - offset)
        books.append(current)
    event = {'schemaVersion': 4, 'minute': minute, 'featureBar': minute // 15,
             'featureId': feed.digest(features), 'features': features,
             'decision': books[0], 'execution': books[1]}
    event['marketId'] = feed.digest({key: value for key, value in event.items() if key != 'features'})
    return event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=runner.ROOT)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    output = (args.output_dir or project / 'data').resolve()
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='intraday-smoke-', dir=output))
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
        environment = {**os.environ, 'HOST': '127.0.0.1', 'PORT': str(ports[mode]),
                       'API_TOKEN': '', 'RISK_POLICY': 'reduce-only-v2', 'LOG_LEVEL': 'error',
                       'DATABASE_PATH': str(directory / (mode + '.sqlite'))}
        processes[mode] = subprocess.Popen(['node', 'dist/app/server.js'], cwd=project,
                                           env=environment, stdout=log, stderr=log)
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
        # Keep synthetic collection and restart within one real minute, away
        # from candle boundaries. Never sleep more than 28 seconds here.
        offset = time.time() % 60
        if offset < 8:
            time.sleep(8 - offset)
        elif offset > 40:
            time.sleep(68 - offset)
        now = time.time()
        event = fixture(now)
        with patch.object(feed, 'collect_book', return_value=event['execution']):
            experiment = runner.initialize(root, urls)
        assert experiment['startBar'] == int(now) // 900 + 1
        assert experiment['startMinute'] == experiment['startBar'] * 15
        assert runner.execute(root, 'slow')['status'] == 'waiting'
        assert runner.execute(root, 'pullback')['status'] == 'waiting'
        checks.append('Two fresh isolated $50 accounts start prospectively at the next 15-minute bar')

        with patch.object(feed, 'collect_book', return_value=event['execution']):
            try:
                runner.initialize(directory / 'alias-experiment', {
                    'slow': urls['slow'], 'pullback': urls['slow'].replace('127.0.0.1', 'localhost'),
                })
                raise AssertionError('Two aliases of one database were accepted')
            except ValueError as error:
                assert 'isolation' in str(error).lower(), str(error)
        checks.append('Different hostname aliases of one SQLite backend fail portfolio isolation')

        # Explicit fixture-only prospective-date override: no production clock
        # or source policy is changed, and no elapsed forward evidence is claimed.
        experiment.update(startBar=event['featureBar'], startMinute=event['featureBar'] * 15,
                          createdAt=(event['featureBar'] - 1) * 900 + 1)
        runner.core.save_json(root / 'experiment.json', experiment)
        (root / 'market').mkdir()
        events = {event['minute']: event}
        runner.core.save_json(root / 'market' / (str(event['minute']) + '.json'), {'syntheticSmokeFixture': True})

        def read_event(_root, minute, feature_cache=None):
            return deepcopy(events.get(minute))

        original_deliver = runner.core.deliver

        def lose_ack(*args, **kwargs):
            original_deliver(*args, **kwargs)
            raise RuntimeError('Synthetic process crash after confirmed server commit')

        with patch.object(feed, 'read_event', side_effect=read_event), \
             patch.object(runner.core, 'analyze', side_effect=AssertionError('Unexpected AI analysis')):
            slow = runner.execute(root, 'slow')
            with patch.object(runner.core, 'deliver', side_effect=lose_ack):
                try:
                    runner.execute(root, 'pullback')
                    raise AssertionError('Synthetic lost acknowledgement did not occur')
                except RuntimeError as error:
                    assert 'Synthetic process crash' in str(error)
            state_path = root / 'pullback' / 'state.json'
            interrupted = runner.core.read_json(state_path)
            assert interrupted['pending'] is not None
            request_path = Path(interrupted['pending']['directory']) / 'state.json'
            completed_request = runner.core.read_json(request_path)
            assert completed_request['complete'] is True
            original_response = completed_request['result']['response']
            assert original_response['status'] == 'executed'
            assert D(original_response['trade']['price']) == D('100.120020')
            assert D(original_response['trade']['grossUsd']) <= 5
            unknown = deepcopy(completed_request)
            unknown.update(complete=False)
            unknown.pop('result')
            runner.core.save_json(request_path, unknown)
            stop('pullback')
            start('pullback')

            # Pending recovery must commit local bookkeeping even when source
            # pin checks then block any new policy work.
            with patch.object(runner, 'version', return_value='v4-synthetic-source-change'):
                try:
                    runner.execute(root, 'pullback')
                    raise AssertionError('Changed source pin accepted fresh work')
                except ValueError as error:
                    assert 'version changed' in str(error).lower(), str(error)
            recovered = runner.core.read_json(state_path)
            retried = runner.core.read_json(request_path)
            assert recovered['pending'] is None and recovered['expectedVersion'] == 1
            assert retried['body'] == completed_request['body'] and retried['key'] == completed_request['key']
            assert retried['result']['replayed'] is True
            assert retried['result']['response']['decisionId'] == original_response['decisionId']
            assert len(runner.core.get_json(urls['pullback'] + '/api/trades')['trades']) == 1
            entry_cycle = runner.execute(root, 'pullback')
            assert entry_cycle['status'] == 'complete'
            assert runner.execute(root, 'pullback')['status'] == 'skipped'
            checks.append('BUY executes at delayed ask plus 2bps and backend fees; no AI decision is called')
            checks.append('Lost acknowledgement, backend restart and changed-source recovery retain exact body/key and one fill')

            with patch.object(runner, 'version', return_value='v4-synthetic-source-change'):
                try:
                    runner.execute(root, 'pullback')
                    raise AssertionError('Changed source pin accepted a fresh cycle')
                except ValueError as error:
                    assert 'version changed' in str(error).lower(), str(error)
            checks.append('Changed source pin blocks all fresh policy work')

            # Force only the disposable confirmed entry age past its predeclared
            # twelve-hour limit. Doubling the bid makes liquidation need two
            # sequential <=$5 SELLs, without a new 15-minute feature requirement.
            target = (int(time.time()) // 60 + 1) * 60 + 8
            # Keep the exchange/book and real backend fill clocks consistent.
            # Short chunks also keep each wait bounded.
            while time.time() < target:
                time.sleep(min(20, target - time.time()))
            exit_now = time.time()
            exit_event = fixture(exit_now, bid='200', ask='200.1')
            events[exit_event['minute']] = exit_event
            runner.core.save_json(root / 'market' / (str(exit_event['minute']) + '.json'), {'syntheticSmokeFixture': True})
            aged = runner.core.read_json(state_path)
            aged['policyState']['positions']['BTCUSDT']['entryTime'] = exit_now - 43201
            runner.core.save_json(state_path, aged)
            exit_cycle = runner.execute(root, 'pullback')
            slow_next = runner.execute(root, 'slow')
            sell_actions = exit_cycle['actions']
            assert len(sell_actions) == 2, sell_actions
            assert all(action['action'] == 'SELL' and action['status'] == 'executed' for action in sell_actions)
            assert all(0 < D(action['trade']['grossUsd']) <= 5 for action in sell_actions)
            assert all(D(action['trade']['price']) == D('199.960000') for action in sell_actions)
            assert 'maximumHold' in exit_cycle.get('reason', '') or all(
                action['ruleReason'] in ('POSITION_EXIT', 'STICKY_EXIT') for action in sell_actions)
            exited = runner.core.read_json(state_path)
            assert 'BTCUSDT' in exited['policyState']['lastExitTime']
            assert 'BTCUSDT' not in exited['closing']
            assert exited['expectedVersion'] == 3
            after = runner.checked_context(urls['pullback'], runner.marks(exit_event['execution']))
            assert runner.quantity(after, 'BTCUSDT') * 200 < D('0.000001')
            _, _, gates, _ = runner.policy.plan(after, exit_event['features'], exit_event['decision'],
                                              'pullback', exited['policyState'], [], True, evaluation_time=time.time())
            assert 'cooldownExpired' in gates['BTCUSDT']['failedGates']
            assert D(gates['BTCUSDT']['cooldownRemainingSeconds']) > 0
            checks.append('A minute risk cycle liquidates a >$5 position in two sequential capped SELLs without a BUY')
            checks.append('Confirmed full exit persists cooldown and leaves only API dust')

            contexts = {mode: runner.checked_context(urls[mode], runner.marks(exit_event['execution']))
                        for mode in runner.MODES}
            report = reporting.build_report(root, now=exit_now, book=exit_event['execution'], contexts=contexts)
            assert report['matchedMarketMinutes'] == 2
            assert report['review']['automaticPromotion'] is False
            assert report['accounts']['pullback']['fills'] == {'BUY': 1, 'SELL': 2}
            assert report['accounts']['pullback']['completedRoundTrips'] == 1
            assert D(report['accounts']['pullback']['feeReconciliationDifferenceUsd']) == 0
            checks.append('Shared-event report reconciles two matched minutes, spread/slippage and ledger fees')

        ledgers = {mode: reconcile(directory / (mode + '.sqlite')) for mode in runner.MODES}
        assert ledgers['pullback']['trades'] == 3 and ledgers['pullback']['completedRoundTrips'] == 1
        for mode in runner.MODES:
            assert ledgers[mode]['trades'] == contexts[mode]['portfolio']['version']
            assert D(report['accounts'][mode]['feeReconciliationDifferenceUsd']) == 0
        checks.append('Both full SQLite ledgers independently reconcile cash, holdings, fills and fees')
        runner.core.save_json(directory / 'verification.json', {
            'status': 'passed', 'checks': checks, 'ledgers': ledgers, 'report': report,
            'entryCycle': entry_cycle, 'exitCycle': exit_cycle, 'slowCycles': [slow, slow_next],
            'syntheticOverrides': ['Temporary experiment starts in current feature bar',
                'Closed features and minute books are controlled policy fixtures via patched read_event',
                'Confirmed entry age is moved past 12h and price doubled to exercise two capped exits',
                'Next-minute exit fixture waits for real clock; backend and quote timestamps remain comparable'],
        })
        print(runner.core.dumps({'status': 'passed', 'directory': str(directory), 'checks': checks}))
    finally:
        for mode in processes:
            stop(mode)
        for log in logs:
            log.close()


if __name__ == '__main__':
    main()
