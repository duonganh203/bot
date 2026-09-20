#!/usr/bin/env python3
"""Pinned, forward-only paper comparison: legacy five-coin rule versus V3."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
import uuid

import run as core
import policy as baseline
import research_policy as candidate_policy
import research_market as feed
from paired import checked_context
from shadow import origin

ROOT = Path(__file__).resolve().parent.parent
MODES = ('baseline', 'candidate')
SYMBOLS = baseline.UNIVERSES['five']


def version():
    names = ('run.py', 'shadow.py', 'paired.py', 'policy.py',
             'research_runner.py', 'research_policy.py', 'research_market.py')
    paths = [ROOT / 'runner' / name for name in names]
    paths += sorted((ROOT / 'src').rglob('*.ts'))
    paths += sorted((ROOT / 'drizzle').rglob('*.sql'))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0'
                      + path.read_bytes().replace(b'\r\n', b'\n'))
    return 'v3-' + digest.hexdigest()[:16]


def check_manifest(experiment):
    core.require(experiment['schemaVersion'] == 3 and experiment['kind'] == 'research-v3',
                 'Unexpected research manifest')
    core.require(experiment['version'] == version(),
                 'Experiment code changed; start a new prospective version explicitly')
    core.require(tuple(experiment['backends']) == MODES
                 and len(set(experiment['backends'].values())) == 2, 'Separate branches required')
    core.require(experiment['symbols'] == list(SYMBOLS), 'Research universe changed')
    core.require(experiment['policies'] == {'baseline': baseline.POLICY, 'candidate': candidate_policy.POLICY},
                 'Research policy parameters changed')
    core.require(experiment['startSlot'] == int(experiment['createdAt']) // 3600 + 1,
                 'Prospective start slot changed')
    core.require(experiment['aiFilter'] is False, 'This experiment does not use an AI entry veto')


def initialize(root, backends):
    root.mkdir(parents=True, exist_ok=True)
    core.require(tuple(backends) == MODES and len(set(backends.values())) == 2,
                 'Separate baseline and candidate backends required')
    backends = {mode: origin(url) for mode, url in backends.items()}
    path = root / 'experiment.json'
    if path.exists():
        existing = core.read_json(path)
        check_manifest(existing)
        core.require(existing['backends'] == backends, 'Existing backend configuration differs')
        return existing
    core.require(not any((root / mode / 'state.json').exists() for mode in MODES),
                 'State exists without an experiment manifest')
    market = feed.collect_market(SYMBOLS)
    contexts = {mode: checked_context(url, market) for mode, url in backends.items()}
    for mode in MODES:
        context = contexts[mode]
        portfolio = context['portfolio']
        core.require(portfolio['version'] == 0 and baseline.D(str(portfolio['cash'])) == 50
                     and not context['positions'], 'Research initialization requires fresh $50 paper accounts')
        core.assert_fresh(market, context, time.time())
        other = next(m for m in MODES if m != mode)
        status, _, _ = core.http('GET', backends[mode] + '/api/contexts/' + contexts[other]['contextId'])
        core.require(status == 404, 'Portfolio isolation failed')
    now = time.time()
    experiment = {
        'schemaVersion': 3, 'kind': 'research-v3', 'version': version(), 'createdAt': now,
        'startSlot': int(now) // 3600 + 1, 'backends': backends, 'symbols': list(SYMBOLS),
        'policies': {'baseline': deepcopy(baseline.POLICY), 'candidate': deepcopy(candidate_policy.POLICY)},
        'openingContexts': contexts, 'aiFilter': False, 'reviewAfterDays': 14,
        'evaluation': 'Prospective experiment, not validated alpha. Compare net equity, turnover, sampled drawdown, exposure, benchmarks and coverage. A review date is not a promotion threshold. No automatic promotion.',
        'costModel': 'Same cached paper quote and 0.1% fee each side. Book spread is observed, not applied to fills. Report separate 5/10 bps turnover sensitivity and operating cost assumptions.',
    }
    core.save_json(path, experiment)
    return experiment


def policy_state(previous, context, market):
    """Apply a completed exit exactly once, including after a recovered response."""
    state = deepcopy(previous.get('policyState', {'lastExitSlot': {}})) if previous else {'lastExitSlot': {}}
    if not previous:
        return state
    response = previous['result']['response']
    payload = json.loads(previous['body'])
    if response['status'] == 'executed' and payload['action'] == 'SELL':
        symbol = payload['symbol']
        quantity = sum(baseline.D(str(p['quantity'])) for p in context['positions'] if p['symbol'] == symbol)
        if quantity * baseline.D(market['symbols'][symbol]['price']) < baseline.MICRO:
            state['lastExitSlot'][symbol] = max(previous['slot'], state['lastExitSlot'].get(symbol, -1))
    return state


def payload_for(candidate, context, market, mode, strategy_version):
    payload = {key: candidate[key] for key in ('action', 'confidence', 'riskLevel', 'rationale')}
    payload.update(contextId=context['contextId'], strategyId='research-' + mode + '-v3',
                   strategyVersion=strategy_version, source='research-paper-' + mode)
    if candidate['action'] != 'HOLD':
        payload.update(symbol=candidate['symbol'], amountUsd=core.api_decimal(candidate['amountUsd']),
                       price=core.api_decimal(market['symbols'][candidate['symbol']]['price']))
    return payload


def execute(root, mode, dry_run=False):
    core.require(mode in MODES, 'Unknown research branch')
    experiment = core.read_json(root / 'experiment.json')
    directory = root / mode
    directory.mkdir(parents=True, exist_ok=True)
    backend = experiment['backends'][mode]
    state_path = directory / 'state.json'
    previous = core.read_json(state_path) if state_path.exists() else None
    if previous:
        core.require(previous['backend'] == backend, 'Saved request backend changed')
        run_dir = Path(previous['runDir']).resolve()
        core.require(run_dir.is_relative_to((directory / 'runs').resolve()), 'Run path escapes research branch')
        if not previous['complete']:
            core.require(not dry_run, 'Resolve pending outcome before dry run')
            # Recovery must use the original body and key even after a source change.
            return core.deliver(directory, previous)
        if not (run_dir / 'result.json').exists():
            core.save_json(run_dir / 'result.json', previous['result'])
    check_manifest(experiment)
    slot = int(time.time()) // 3600
    if slot < experiment['startSlot']:
        return {'status': 'waiting', 'reason': 'Prospective experiment starts next hour', 'slot': slot}
    if previous and previous['slot'] >= slot:
        return {'status': 'skipped', 'reason': 'Hourly slot completed', 'slot': slot}
    event = feed.read_event(root / 'market', slot)
    if not event:
        return {'status': 'waiting', 'reason': 'No research market event this hour', 'slot': slot}
    market = event['market']
    core.require(set(market['symbols']) == set(SYMBOLS), 'Research market universe differs')
    core.require(0 <= time.time() - market['startedAt'] < core.MAX_AGE - 45, 'Research market event expired')
    context = checked_context(backend, market)
    core.assert_fresh(market, context, time.time())
    if previous:
        executed = previous['result']['response']['status'] == 'executed'
        expected = previous['contextVersion'] + int(executed)
        core.require(context['portfolio']['version'] == expected, 'Portfolio changed outside the research runner')
    else:
        core.require(context['portfolio']['version'] == 0, 'Research account changed before its first decision')
    state = policy_state(previous, context, market)
    closing = previous.get('closing', []) if previous else []
    if mode == 'baseline':
        candidate, closing, gates, reason = baseline.plan(context, market, closing, SYMBOLS)
    else:
        candidate, closing, gates, reason = candidate_policy.plan(context, market, closing, state)
    run_dir = directory / 'runs' / (str(slot) + '-' + uuid.uuid4().hex)
    run_dir.mkdir(parents=True)
    payload = payload_for(candidate, context, market, mode, experiment['version'])
    evidence = {'slot': slot, 'mode': mode, 'marketId': event['marketId'], 'version': experiment['version'],
                'market': market, 'context': context, 'gates': gates, 'ruleReason': reason,
                'ruleCandidate': candidate, 'candidate': candidate, 'aiVote': None,
                'policyState': state, 'payload': payload}
    core.save_json(run_dir / 'evidence.json', evidence)
    latest = checked_context(backend, market)
    core.require(latest['portfolio']['version'] == context['portfolio']['version'], 'Portfolio changed during decision')
    core.assert_fresh(market, context, time.time())
    if dry_run:
        return {'status': 'dry-run', 'submitted': False, 'payload': payload, 'runDir': str(run_dir)}
    now = time.time()
    expires = min(market['startedAt'] + core.MAX_AGE, (slot + 1) * 3600)
    core.require(int(now) // 3600 == slot and now + core.HTTP_TIMEOUT < expires, 'Event/slot expired')
    request = {'complete': False, 'backend': backend, 'slot': slot, 'slotSeconds': 3600,
               'runDir': str(run_dir), 'expiresAt': expires, 'key': str(uuid.uuid4()),
               'body': core.dumps(payload), 'closing': closing, 'marketId': event['marketId'],
               'contextVersion': context['portfolio']['version'], 'policyState': state}
    core.save_json(run_dir / 'request.json', request)
    core.save_json(state_path, request)
    return core.deliver(directory, request)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'paper-research-v3')
    parser.add_argument('--mode', choices=MODES)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--baseline-backend', default='http://127.0.0.1:3004')
    parser.add_argument('--candidate-backend', default='http://127.0.0.1:3005')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    core.require(sum((args.initialize, bool(args.mode))) == 1, 'Choose initialization or a mode')
    core.require(not (args.initialize and args.dry_run), 'Initialization cannot be a dry run')
    core.os.umask(0o077)
    root = args.root.resolve()
    directory = root if args.initialize else root / args.mode
    directory.mkdir(parents=True, exist_ok=True)
    with core.exclusive_lock(directory):
        result = initialize(root, {'baseline': args.baseline_backend, 'candidate': args.candidate_backend}) \
            if args.initialize else execute(root, args.mode, args.dry_run)
        print(core.dumps(result), flush=True)


if __name__ == '__main__':
    main()
