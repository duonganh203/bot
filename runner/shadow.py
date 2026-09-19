#!/usr/bin/env python3
"""Forward-only deterministic paper control, using the completed AI run's input."""

import argparse
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import uuid

import run as core

D = Decimal
MICRO = D('0.000001')
POLICY = {
    'name': 'trend-control-v1', 'initialCapitalUsd': '50', 'orderUsd': '5',
    'maxExposureUsd': '20', 'feeRate': '0.001', 'dailyLossUsd': '3',
    'entry': 'quote > SMA20 > SMA50 and closed-candle 24h return > 0',
    'exit': 'quote < SMA20 and closed-candle 24h return < 0; finish partial exits',
    'sizing': 'one $5 entry per symbol; no adding; one action per hourly slot',
    'priority': 'exits before entries; BTCUSDT before ETHUSDT for ties',
    'costs': 'same paper quote and 0.1% fee as AI; no spread/slippage',
}


def version():
    digest = hashlib.sha256(Path(__file__).read_bytes() + Path(core.__file__).read_bytes())
    return 'v1-' + digest.hexdigest()[:12]


def origin(value):
    value = value.rstrip('/')
    url = urlsplit(value)
    core.require(url.scheme in ('http', 'https') and url.hostname and not url.username
                 and not url.password and not url.path and not url.query and not url.fragment,
                 'Expected an HTTP(S) origin without credentials or path')
    return value


def state_source(directory, baseline):
    state_path = directory / 'state.json'
    if not state_path.exists():
        return None
    state = core.read_json(state_path)
    core.require(state['backend'] == baseline, 'AI state belongs to a different backend')
    if not state['complete']:
        return None
    run_dir = Path(state['runDir']).resolve()
    core.require(run_dir.is_relative_to((directory / 'runs').resolve()), 'AI run path escapes runs directory')
    source = core.read_json(run_dir / 'snapshot.json')
    body = json.loads(state['body'])
    core.require(body['contextId'] == source['context']['contextId'], 'AI snapshot/context mismatch')
    core.require(body['strategyId'] == 'codex-trend', 'Unexpected AI strategy')
    core.require(state.get('slotSeconds') == core.SLOT_SECONDS, 'Requires hourly AI runner')
    core.require(state['result']['response']['status'] in ('held', 'executed', 'rejected'), 'Incomplete AI outcome')
    uuid.UUID(state['result']['response']['decisionId'])
    return state, source, body


def validate_market(market, symbols=core.SYMBOLS):
    """Recompute indicators instead of trusting cached indicator strings."""
    result = deepcopy(market)
    core.require(bool(symbols) and set(symbols) <= set(core.SUPPORTED_SYMBOLS), 'Unsupported market universe')
    for symbol in symbols:
        quote = result['symbols'][symbol]
        rows = [list(row) + [int(row[0]) + core.HOUR_MS - 1]
                for row in quote['closedHourlyCandles']]
        candles = core.closed_candles(rows, int(result['serverTimeMs']))
        closes = [D(row[4]) for row in candles]
        actual = {'sma20': sum(closes[-20:]) / 20, 'sma50': sum(closes[-50:]) / 50,
                  'return24hPct': (closes[-1] / closes[-25] - 1) * 100}
        core.api_decimal(quote['price'])
        for key, value in actual.items():
            core.require(D(quote[key]) == value, 'AI indicator/candle mismatch: ' + key)
            quote[key] = str(value)
    return result


def separate_context(backend, baseline, source, market):
    core.require(backend != baseline, 'Control and AI backends must be different')
    context = core.fetch_context(backend, market)
    # Different ports/hostnames alone cannot prove separate portfolio databases.
    status, _, _ = core.http('GET', backend + '/api/contexts/' + source['context']['contextId'])
    core.require(status == 404, 'Control backend contains the AI context; isolation not established')
    status, _, _ = core.http('GET', baseline + '/api/contexts/' + context['contextId'])
    core.require(status == 404, 'AI backend contains the control context; isolation not established')
    risk = context['risk']
    for field, expected in [('maxOrderUsd', '5'), ('maxExposureUsd', '20'),
                            ('maxDailyLossUsd', '3'), ('feeRate', '0.001')]:
        core.require(D(str(risk[field])) == D(expected), 'Control risk policy differs: ' + field)
    core.require(D(str(context['portfolio']['initialCapital'])) == 50, 'Control capital must be $50')
    return context


def plan(context, market, closing):
    positions = {p['symbol']: D(str(p['quantity'])) for p in context['positions']}
    marks = {s: D(market['symbols'][s]['price']) for s in core.SYMBOLS}
    holdings = {s: positions.get(s, D(0)) * marks[s] for s in core.SYMBOLS}
    # Sub-microdollar API dust remains in backend accounting and valuation.
    closing = {s for s in closing if holdings[s] >= MICRO}
    gates = {}
    for symbol in core.SYMBOLS:
        quote = market['symbols'][symbol]
        gates[symbol] = {
            'entry': marks[symbol] > D(quote['sma20']) > D(quote['sma50']) and D(quote['return24hPct']) > 0,
            'exit': marks[symbol] < D(quote['sma20']) and D(quote['return24hPct']) < 0,
            'holdingUsd': str(holdings[symbol]),
        }
        if holdings[symbol] >= MICRO and gates[symbol]['exit']:
            closing.add(symbol)
    candidate = {'action': 'HOLD', 'symbol': None, 'amountUsd': None, 'confidence': 1.0,
                 'riskLevel': 'LOW', 'rationale': 'Fixed control: no eligible entry or exit.'}
    reason = 'NO_ELIGIBLE_ENTRY'
    if context['risk']['dailyLossLimitReached']:
        candidate['rationale'] = 'Fixed control: backend daily-loss gate blocks orders, including sells.'
        return candidate, sorted(closing), gates, 'DAILY_LOSS_LIMIT'
    for symbol in core.SYMBOLS:
        if symbol in closing:
            amount = min(D('5'), holdings[symbol]).quantize(MICRO, rounding=ROUND_DOWN)
            candidate.update(action='SELL', symbol=symbol, amountUsd=str(amount),
                             rationale='Fixed control: weak trend triggered exit; complete remaining position in $5 maximum steps.')
            return candidate, sorted(closing), gates, 'TREND_EXIT'
    for symbol in core.SYMBOLS:
        if not gates[symbol]['entry'] or holdings[symbol] >= MICRO:
            continue
        if D(str(context['portfolio']['cash'])) < D('5.005'):
            reason = 'INSUFFICIENT_CASH'
            continue
        if sum(holdings.values()) + 5 > 20:
            reason = 'MAX_EXPOSURE'
            continue
        candidate.update(action='BUY', symbol=symbol, amountUsd='5',
                         rationale='Fixed control: quote > SMA20 > SMA50 and 24h return > 0; one $5 entry, no adding.')
        return candidate, sorted(closing), gates, 'TREND_ENTRY'
    candidate['rationale'] = 'Fixed control: ' + reason + '; no order.'
    return candidate, sorted(closing), gates, reason


def alignment(candidate, ai_body, ai_result, context, source):
    def inventory(c):
        return {p['symbol']: str(D(str(p['quantity'])).normalize()) for p in c['positions']}
    comparable = (inventory(context) == inventory(source['context'])
                  and D(str(context['portfolio']['cash'])) == D(str(source['context']['portfolio']['cash'])))
    same = candidate['action'] == ai_body['action'] and candidate['symbol'] == ai_body.get('symbol')
    return {'aiDecisionId': ai_result['response']['decisionId'], 'aiStrategyVersion': ai_body['strategyVersion'],
            'aiAction': ai_body['action'], 'aiSymbol': ai_body.get('symbol'),
            'aiStatus': ai_result['response']['status'], 'aiRationale': ai_body['rationale'],
            'sameActionAndSymbol': same, 'samePreDecisionCashAndInventory': comparable,
            'classification': ('same_action' if same else 'ai_held' if ai_body['action'] == 'HOLD' else 'different_action'),
            'limitation': 'Observed AI action on its own portfolio, not a vote on the control portfolio; sizing can differ.'}


def initialize(directory, source_dir, backend, baseline):
    path = directory / 'experiment.json'
    if path.exists():
        existing = core.read_json(path)
        core.require(existing['backend'] == backend and existing['baseline'] == baseline
                     and existing['strategyVersion'] == version(), 'Existing experiment configuration differs')
        return existing
    core.require(not (directory / 'state.json').exists(), 'State exists without an experiment; do not reinitialize')
    source_data = state_source(source_dir, baseline)
    core.require(source_data is not None, 'Need a completed AI run before initialization')
    _, source, ai_body = source_data
    market = core.collect_market()
    context = separate_context(backend, baseline, source, market)
    portfolio = context['portfolio']
    core.require(portfolio['version'] == 0 and D(str(portfolio['cash'])) == 50
                 and not context['positions'], 'Initialize requires a fresh separate $50 control portfolio')
    experiment = {'schemaVersion': 1, 'createdAt': time.time(), 'startSlot': int(time.time()) // 3600 + 1,
                  'backend': backend, 'baseline': baseline, 'sourceDir': str(source_dir.resolve()),
                  'strategyVersion': version(), 'aiStrategyVersion': ai_body['strategyVersion'],
                  'policy': POLICY, 'reviewAfterDays': 14,
                  'evaluation': 'Review equity, drawdown, costs, completed round trips, coverage, and action agreement after 14 calendar days. No automatic promotion or forced trade count.'}
    core.save_json(path, experiment)
    return experiment


def execute(directory, source_dir, backend, baseline, dry_run=False):
    experiment = core.read_json(directory / 'experiment.json')
    core.require(experiment['backend'] == backend and experiment['baseline'] == baseline
                 and experiment['sourceDir'] == str(source_dir.resolve()), 'Experiment endpoints/source differ')
    state_path = directory / 'state.json'
    previous = core.read_json(state_path) if state_path.exists() else None
    if previous:
        core.require(previous['backend'] == backend, 'State belongs to a different control backend')
        previous_dir = Path(previous['runDir']).resolve()
        core.require(previous_dir.is_relative_to((directory / 'runs').resolve()), 'Control run path escapes runs directory')
        if not previous['complete']:
            core.require(not dry_run, 'Resolve pending request before dry run')
            return core.deliver(directory, previous)
        # A crash after committing complete state but before the audit copy must
        # not lose that result when the next hour replaces state.json.
        if not (previous_dir / 'result.json').exists():
            core.save_json(previous_dir / 'result.json', previous['result'])
    core.require(experiment['strategyVersion'] == version(), 'Control code changed; use a new experiment/version')
    now = time.time()
    slot = int(now) // core.SLOT_SECONDS
    if slot < experiment['startSlot']:
        return {'status': 'waiting', 'reason': 'Experiment starts at the next hourly slot', 'startSlot': experiment['startSlot']}
    if previous and previous['slot'] >= slot:
        return {'status': 'skipped', 'reason': 'This hourly slot already completed'}
    loaded = state_source(source_dir, baseline)
    if loaded is None or loaded[0]['slot'] != slot:
        return {'status': 'waiting', 'reason': 'No completed AI run for the current hour'}
    ai_state, source, ai_body = loaded
    core.require(ai_body['strategyVersion'] == experiment['aiStrategyVersion'],
                 'AI strategy version changed; review comparison before continuing')
    market = validate_market(source['market'])
    core.require(market['serverTimeMs'] // core.HOUR_MS == slot, 'AI candle hour mismatch')
    core.assert_fresh(market, source['context'], now)
    context = separate_context(backend, baseline, source, market)
    candidate, closing, gates, reason = plan(context, market, previous.get('closing', []) if previous else [])
    payload = core.make_payload(candidate, context, market, experiment['strategyVersion'])
    payload.update(strategyId='trend-control', source='deterministic-paper-control')
    comparison = alignment(candidate, ai_body, ai_state['result'], context, source)
    run_dir = directory / 'runs' / (str(slot) + '-' + uuid.uuid4().hex)
    run_dir.mkdir(parents=True)
    evidence = {'slot': slot, 'sourceRunDir': ai_state['runDir'], 'sourceContextId': source['context']['contextId'],
                'market': market, 'context': context, 'gates': gates, 'ruleReason': reason,
                'candidate': candidate, 'comparison': comparison, 'payload': payload}
    core.save_json(run_dir / 'evidence.json', evidence)
    if dry_run:
        return {'status': 'dry-run', 'submitted': False, 'payload': payload, 'comparison': comparison}
    latest = core.fetch_context(backend, market)
    core.require(latest['portfolio']['version'] == context['portfolio']['version'], 'Control portfolio changed during decision')
    now = time.time()
    core.assert_fresh(market, context, now)
    core.require(int(now) // 3600 == slot, 'Hourly slot changed; no control order submitted')
    expires = min(market['startedAt'] + core.MAX_AGE, (slot + 1) * 3600)
    core.require(now + core.HTTP_TIMEOUT < expires, 'Control snapshot too close to expiry')
    state = {'complete': False, 'backend': backend, 'slot': slot, 'slotSeconds': 3600,
             'runDir': str(run_dir), 'expiresAt': expires, 'key': str(uuid.uuid4()),
             'body': core.dumps(payload), 'closing': closing}
    core.save_json(run_dir / 'request.json', state)
    core.save_json(state_path, state)
    return core.deliver(directory, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path.home() / 'paper-shadow' / 'state')
    parser.add_argument('--source-dir', type=Path, default=Path.home() / 'paper-runner' / 'state')
    parser.add_argument('--backend', default='http://127.0.0.1:3001')
    parser.add_argument('--baseline', default='http://127.0.0.1:3000')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    core.require(not (args.initialize and args.dry_run), 'Choose initialize or dry-run')
    backend, baseline = origin(args.backend), origin(args.baseline)
    directory, source_dir = args.state_dir.resolve(), args.source_dir.resolve()
    core.require(backend != baseline, 'Control and AI backends must differ')
    core.require(not directory.is_relative_to(source_dir) and not source_dir.is_relative_to(directory),
                 'Control and AI state directories must be separate')
    core.os.umask(0o077)
    directory.mkdir(parents=True, exist_ok=True)
    with core.exclusive_lock(directory):
        result = (initialize(directory, source_dir, backend, baseline) if args.initialize
                  else execute(directory, source_dir, backend, baseline, args.dry_run))
        print(core.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, core.InvalidOperation, RuntimeError) as error:
        print('SHADOW_FAILED: ' + str(error), file=sys.stderr, flush=True)
        sys.exit(1)
