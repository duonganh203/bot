#!/usr/bin/env python3
"""V2: independent consumers of one market event, sharing portfolio policy."""
import argparse
import hashlib
from pathlib import Path
import time
import uuid

import run as core
import policy
import entry_filter
from market_snapshot import read_event
from shadow import origin

ROOT = Path(__file__).resolve().parent.parent
MODES = ('ai', 'control')


def version():
    paths = [ROOT / 'runner' / name for name in (
        'run.py', 'shadow.py', 'policy.py', 'market_snapshot.py', 'paired.py',
        'entry_filter.py', 'entry-filter.md', 'entry-filter.schema.json')]
    paths += sorted((ROOT / 'src').rglob('*.ts'))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0' + path.read_bytes().replace(b'\r\n', b'\n'))
    return 'v2-' + digest.hexdigest()[:16]


def checked_context(backend, market):
    context = core.fetch_context(backend, market)
    risk = context['risk']
    core.require(risk['policy'] == 'reduce-only-v2' and risk['reducingSellsAllowed'], 'V2 backend risk policy required')
    for field in ('maxOrderUsd', 'maxExposureUsd', 'maxDailyLossUsd', 'maxEquityLossUsd', 'feeRate'):
        expected = policy.POLICY['orderUsd' if field == 'maxOrderUsd' else field]
        core.require(policy.D(str(risk[field])) == policy.D(expected), 'Risk limits differ: ' + field)
    core.require(policy.D(str(context['portfolio']['initialCapital'])) == 50, 'Initial capital differs')
    return context


def initialize(root, backends):
    path = root / 'experiment.json'
    if path.exists():
        existing = core.read_json(path)
        core.require(existing['backends'] == backends and existing['version'] == version(), 'Existing experiment differs')
        return existing
    core.require(backends['ai'] != backends['control'], 'Separate backends required')
    core.require(not any((root / mode / 'state.json').exists() for mode in MODES), 'State exists without manifest')
    market = core.collect_market()
    contexts = {mode: checked_context(url, market) for mode, url in backends.items()}
    for mode in MODES:
        p = contexts[mode]['portfolio']
        core.require(p['version'] == 0 and policy.D(str(p['cash'])) == 50 and not contexts[mode]['positions'],
                     'V2 initialization requires two fresh $50 paper accounts; keep V1 separately')
        other = 'ai' if mode == 'control' else 'control'
        status, _, _ = core.http('GET', backends[mode] + '/api/contexts/' + contexts[other]['contextId'])
        core.require(status == 404, 'Portfolio isolation failed')
    experiment = {'schemaVersion': 2, 'version': version(), 'createdAt': time.time(),
                  'startSlot': int(time.time()) // 3600 + 1, 'backends': backends,
                  'policy': policy.POLICY, 'openingContexts': contexts, 'reviewAfterDays': 14,
                  'evaluation': 'Forward only; net equity, sampled drawdown, fees, round trips, coverage and AI vetoes. Do not pool V1 results or promote automatically.'}
    core.save_json(path, experiment)
    return experiment


def payload_for(candidate, context, market, mode, strategy_version):
    payload = {key: candidate[key] for key in ('action', 'confidence', 'riskLevel', 'rationale')}
    payload.update(contextId=context['contextId'], strategyId='trend-' + mode + '-v2',
                   strategyVersion=strategy_version, source='paired-paper-' + mode)
    if candidate['action'] != 'HOLD':
        payload.update(symbol=candidate['symbol'], amountUsd=core.api_decimal(candidate['amountUsd']),
                       price=core.api_decimal(market['symbols'][candidate['symbol']]['price']))
    return payload


def execute(root, mode, dry_run=False, codex='codex'):
    experiment = core.read_json(root / 'experiment.json')
    directory = root / mode
    directory.mkdir(parents=True, exist_ok=True)
    backend = experiment['backends'][mode]
    state_path = directory / 'state.json'
    previous = core.read_json(state_path) if state_path.exists() else None
    if previous:
        core.require(previous['backend'] == backend, 'Saved request backend changed')
        run_dir = Path(previous['runDir']).resolve()
        core.require(run_dir.is_relative_to((directory / 'runs').resolve()), 'Run path escapes experiment')
        if not previous['complete']:
            core.require(not dry_run, 'Resolve pending outcome before dry run')
            return core.deliver(directory, previous)
        if not (run_dir / 'result.json').exists():
            core.save_json(run_dir / 'result.json', previous['result'])
    core.require(experiment['version'] == version(), 'Experiment code changed; explicitly start a new version')
    slot = int(time.time()) // 3600
    if slot < experiment['startSlot']:
        return {'status': 'waiting', 'reason': 'Experiment starts next hour', 'slot': slot}
    if previous and previous['slot'] >= slot:
        return {'status': 'skipped', 'reason': 'Hourly slot completed', 'slot': slot}
    event = read_event(root / 'market', slot)
    if not event:
        return {'status': 'waiting', 'reason': 'No market event this hour', 'slot': slot}
    market = event['market']
    core.require(0 <= time.time() - market['startedAt'] < core.MAX_AGE - 45, 'Market event expired')
    context = checked_context(backend, market)
    core.assert_fresh(market, context, time.time())
    candidate, closing, gates, reason = policy.plan(context, market, previous.get('closing', []) if previous else [])
    rule_candidate = dict(candidate)
    run_dir = directory / 'runs' / (str(slot) + '-' + uuid.uuid4().hex)
    run_dir.mkdir(parents=True)
    evidence = {'slot': slot, 'mode': mode, 'marketId': event['marketId'], 'version': experiment['version'],
                'market': market, 'context': context, 'gates': gates, 'ruleReason': reason,
                'ruleCandidate': rule_candidate, 'aiVote': None}
    core.save_json(run_dir / 'evidence.json', evidence)
    if mode == 'ai' and candidate['action'] == 'BUY':
        try:
            vote = entry_filter.vote(run_dir, market, candidate, codex)
            evidence['aiVote'] = {'status': 'ok', **vote}
            if not vote['approve']:
                candidate = policy.hold('AI_ENTRY_VETO: ' + vote['rationale'])
            else:
                candidate['rationale'] += ' AI approved: ' + vote['rationale']
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
            evidence['aiVote'] = {'status': 'error', 'error': str(error)}
            candidate = policy.hold('AI_UNAVAILABLE: entry skipped; see audit logs.')
    evidence['candidate'] = candidate
    payload = payload_for(candidate, context, market, mode, experiment['version'])
    evidence['payload'] = payload
    core.save_json(run_dir / 'evidence.json', evidence)
    core.assert_fresh(market, context, time.time())
    latest = checked_context(backend, market)
    core.require(latest['portfolio']['version'] == context['portfolio']['version'], 'Portfolio changed during decision')
    core.assert_fresh(market, context, time.time())
    if dry_run:
        return {'status': 'dry-run', 'submitted': False, 'payload': payload, 'runDir': str(run_dir)}
    now = time.time()
    expires = min(market['startedAt'] + core.MAX_AGE, (slot + 1) * 3600)
    core.require(int(now) // 3600 == slot and now + core.HTTP_TIMEOUT < expires, 'Event/slot expired')
    state = {'complete': False, 'backend': backend, 'slot': slot, 'slotSeconds': 3600,
             'runDir': str(run_dir), 'expiresAt': expires, 'key': str(uuid.uuid4()),
             'body': core.dumps(payload), 'closing': closing, 'marketId': event['marketId']}
    core.save_json(run_dir / 'request.json', state)
    core.save_json(state_path, state)
    return core.deliver(directory, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'paper-v2')
    parser.add_argument('--mode', choices=MODES)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--ai-backend', default='http://127.0.0.1:3000')
    parser.add_argument('--control-backend', default='http://127.0.0.1:3001')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--codex', default='codex')
    args = parser.parse_args()
    core.require(args.initialize != bool(args.mode), 'Choose initialize or a mode')
    core.require(not (args.initialize and args.dry_run), 'Initialization cannot be a dry run')
    core.os.umask(0o077)
    root = args.root.resolve()
    directory = root if args.initialize else root / args.mode
    directory.mkdir(parents=True, exist_ok=True)
    with core.exclusive_lock(directory):
        result = (initialize(root, {'ai': origin(args.ai_backend), 'control': origin(args.control_backend)})
                  if args.initialize else execute(root, args.mode, args.dry_run, args.codex))
        print(core.dumps(result), flush=True)


if __name__ == '__main__':
    main()
