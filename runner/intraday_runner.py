#!/usr/bin/env python3
"""Prospective V4 paper comparison with durable, sequential risk exits."""
import argparse
from copy import deepcopy
from datetime import datetime
from decimal import Decimal as D, ROUND_DOWN, ROUND_UP
import hashlib
import json
from pathlib import Path
import time
import uuid

import run as core
from shadow import origin
import intraday_market as feed
import intraday_policy as policy

ROOT = Path(__file__).resolve().parent.parent
MODES = ('slow', 'pullback')
MICRO = D('0.000001')
EXECUTION = {
    'feeRate': '0.001', 'adverseSlippageBps': '2', 'minimumQuoteDelaySeconds': 2,
    'maximumExecutionQuoteAgeSeconds': 45, 'maximumActionsPerMinute': 10,
    'fills': 'BUY delayed best ask plus 2bps, SELL delayed best bid minus 2bps; adverse six-decimal rounding',
    'liquidity': 'Displayed top quantity is a per-symbol/side cycle budget; no queue or full-book simulation',
    'valuation': 'Bid marks, before hypothetical liquidation fees; execution fees are charged by backend',
    'recovery': 'Exact durable body/key only; an expired unknown outcome blocks new work for reconciliation',
    'status': 'Paper assumptions, not measured live execution or evidence of profitable alpha',
}


def exact_http(method, url, body=None, key=None):
    """Request decimal strings so paper quantity never passes through a float."""
    headers = {'Accept': 'application/json', 'X-Decimal-Format': 'string',
               'User-Agent': 'ai-paper-intraday-v4'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if key:
        headers['Idempotency-Key'] = key
    request = core.Request(url, data=body.encode() if body is not None else None, headers=headers, method=method)
    try:
        response = core.urlopen(request, timeout=core.HTTP_TIMEOUT)
    except core.HTTPError as error:
        response = error
    with response:
        raw = response.read(2_000_001)
        core.require(len(raw) <= 2_000_000, 'Oversized backend response')
        return response.status, dict(response.headers.items()), json.loads(raw)


def checked_context(backend, market):
    query = core.urlencode({s: q['price'] for s, q in market['symbols'].items()})
    status, _, context = exact_http('GET', backend + '/api/context?' + query)
    core.require(status == 200, 'Failed to fetch exact backend context')
    uuid.UUID(context['contextId'])
    core.require(type(context['portfolio']['version']) is int, 'Invalid portfolio version')
    risk = context['risk']
    core.require(risk['policy'] == 'reduce-only-v2' and risk['reducingSellsAllowed'], 'Reduce-only backend required')
    for field in ('maxOrderUsd', 'maxExposureUsd', 'maxDailyLossUsd', 'maxEquityLossUsd', 'feeRate'):
        core.require(D(str(risk[field])) == D(policy.POLICY[field]), 'Backend risk limits differ: ' + field)
    core.require(context['portfolio']['initialCapital'] == '50' and isinstance(context['portfolio']['cash'], str),
                 'Exact decimal backend with $50 capital required')
    core.require(all(isinstance(p['quantity'], str) for p in context['positions']), 'Exact position quantities required')
    return context


def version():
    names = ('run.py', 'shadow.py', 'paired.py', 'policy.py', 'research_market.py', 'research_policy.py',
             'intraday_market.py', 'intraday_policy.py', 'intraday_runner.py')
    paths = [ROOT / 'runner' / name for name in names]
    paths += sorted((ROOT / 'src').rglob('*.ts')) + sorted((ROOT / 'drizzle').rglob('*.sql'))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0' +
                      path.read_bytes().replace(b'\r\n', b'\n'))
    return 'v4-' + digest.hexdigest()[:16]


def check_manifest(experiment):
    core.require(experiment['schemaVersion'] == 4 and experiment['kind'] == 'intraday-v4', 'Wrong experiment')
    core.require(experiment['version'] == version(), 'Source version changed; create a new prospective experiment')
    core.require(experiment['policy'] == policy.POLICY and experiment['execution'] == EXECUTION, 'Parameters changed')
    core.require(tuple(experiment['backends']) == MODES and len(set(experiment['backends'].values())) == 2,
                 'Separate ordered branches required')
    core.require(experiment['symbols'] == list(policy.SYMBOLS), 'Universe changed')
    core.require(experiment['startBar'] == int(experiment['createdAt']) // 900 + 1 and
                 experiment['startMinute'] == experiment['startBar'] * 15, 'Prospective start changed')


def marks(book, override=None):
    values = {s: {'price': core.api_decimal(D(book['symbols'][s]['bid']).quantize(MICRO, rounding=ROUND_DOWN))}
              for s in policy.SYMBOLS}
    if override:
        symbol, price = override
        values[symbol]['price'] = core.api_decimal(price)
    return {'startedAt': book['startedAt'], 'symbols': values}


def initialize(root, backends):
    root.mkdir(parents=True, exist_ok=True)
    backends = {mode: origin(url) for mode, url in backends.items()}
    core.require(tuple(backends) == MODES and len(set(backends.values())) == 2, 'Separate branches required')
    path = root / 'experiment.json'
    if path.exists():
        saved = core.read_json(path)
        check_manifest(saved)
        core.require(saved['backends'] == backends, 'Backends changed')
        return saved
    core.require(not any((root / mode / 'state.json').exists() for mode in MODES), 'State without manifest')
    book = feed.collect_book()
    contexts = {mode: checked_context(url, marks(book)) for mode, url in backends.items()}
    for mode in MODES:
        c = contexts[mode]
        core.require(c['portfolio']['version'] == 0 and D(c['portfolio']['cash']) == 50 and not c['positions'],
                     'Fresh $50 accounts required')
        other = next(m for m in MODES if m != mode)
        status, _, _ = core.http('GET', backends[mode] + '/api/contexts/' + contexts[other]['contextId'])
        core.require(status == 404, 'Portfolio isolation failed')
    now = time.time()
    experiment = {'schemaVersion': 4, 'kind': 'intraday-v4', 'version': version(), 'createdAt': now,
                  'startBar': int(now) // 900 + 1, 'startMinute': (int(now) // 900 + 1) * 15,
                  'backends': backends, 'symbols': list(policy.SYMBOLS), 'policy': deepcopy(policy.POLICY),
                  'execution': deepcopy(EXECUTION), 'openingContexts': contexts, 'aiFilter': False,
                  'reviewAfterDays': 14,
                  'evaluation': 'Forward paper only. Compare entry rules under common exits, sizing and execution. Original V3 is retained separately. No automatic promotion.'}
    core.save_json(path, experiment)
    return experiment


def new_state(backend):
    return {'backend': backend, 'expectedVersion': 0, 'policyState': {'positions': {}, 'lastExitTime': {}},
            'closing': [], 'lastMinute': -1, 'entryBarProcessed': -1, 'pending': None, 'cycle': None}


def quantity(context, symbol):
    return sum((D(p['quantity']) for p in context['positions'] if p['symbol'] == symbol), D(0))


def execution_order(candidate, context, event, cycle, now):
    """A later quote can cancel/cap the selected action, never select its signal."""
    symbol, side = candidate['symbol'], candidate['action']
    q = event['execution']['symbols'][symbol]
    age = now - q['receivedAt']
    core.require(0 <= age <= EXECUTION['maximumExecutionQuoteAgeSeconds'], 'Execution quote expired')
    slip = D(EXECUTION['adverseSlippageBps']) / 10000
    reference = D(q['ask' if side == 'BUY' else 'bid'])
    price = (reference * (1 + slip if side == 'BUY' else 1 - slip)).quantize(
        MICRO, rounding=ROUND_UP if side == 'BUY' else ROUND_DOWN)
    core.require(price > 0, 'Execution price rounds to zero')
    budget_key = symbol + ':' + side
    liquidity = D(q['askQty' if side == 'BUY' else 'bidQty']) - D(cycle['liquidityUsed'].get(budget_key, '0'))
    if liquidity <= 0:
        return None, 'TOP_BOOK_EXHAUSTED'
    if side == 'SELL':
        amount = min(D(5), quantity(context, symbol) * price, liquidity * price).quantize(MICRO, rounding=ROUND_DOWN)
        if amount < MICRO:
            return None, 'SELL_BELOW_API_PRECISION'
    else:
        amount = D(candidate['amountUsd']).quantize(MICRO, rounding=ROUND_DOWN)
        if amount / price > liquidity:
            return None, 'INSUFFICIENT_TOP_BOOK'
        cash = D(context['portfolio']['cash'])
        exposure = sum((D(p['quantity']) * D(event['execution']['symbols'][p['symbol']]['bid'])
                        for p in context['positions']), D(0))
        equity_after = cash + exposure - amount * (1 + D(EXECUTION['feeRate'])) + amount / price * D(q['bid'])
        if (cash < amount * (1 + D(EXECUTION['feeRate'])) or exposure + amount > 20 or
                equity_after <= D(context['portfolio']['initialCapital']) - 3 or
                context['risk']['dailyLossLimitReached']):
            return None, 'EXECUTION_RISK_RECHECK'
        if price - 2 * D(event['features']['symbols'][symbol]['atr14_15m']) <= 0:
            return None, 'INVALID_FIXED_STOP'
    decision = event['decision']['symbols'][symbol]
    mid = (D(decision['bid']) + D(decision['ask'])) / 2
    telemetry = {'model': 'delayed-top-book-plus-adverse-slip', 'modeledSlippageBps': '2',
                 'decisionQuoteReceivedAt': decision['receivedAt'], 'executionQuoteReceivedAt': q['receivedAt'],
                 'submissionAt': now,
                 'decisionBid': decision['bid'], 'decisionAsk': decision['ask'],
                 'executionBid': q['bid'], 'executionAsk': q['ask'], 'executionReferencePrice': str(reference),
                 'decisionToExecutionQuoteMs': round((q['receivedAt'] - decision['receivedAt']) * 1000, 3),
                 'submissionQuoteAgeMs': round(age * 1000, 3),
                 'executionSpreadBps': str((D(q['ask']) - D(q['bid'])) / ((D(q['ask']) + D(q['bid'])) / 2) * 10000),
                 'adverseFillVsDecisionMidBps': str((price / mid - 1) * (10000 if side == 'BUY' else -10000))}
    return {'price': str(price), 'amountUsd': str(amount), 'execution': telemetry, 'budgetKey': budget_key}, None


def recover_pending(directory, state):
    pending = state['pending']
    if not pending:
        return
    action_dir = Path(pending['directory']).resolve()
    core.require(action_dir.is_relative_to((directory / 'requests').resolve()), 'Request path escapes branch')
    request = core.read_json(action_dir / 'state.json')
    core.require(request['backend'] == state['backend'], 'Pending backend changed')
    if not request['complete']:
        core.deliver(action_dir, request, transport=exact_http)
        request = core.read_json(action_dir / 'state.json')
    result = request['result']['response']
    action = {**pending['action'], 'status': result['status'], 'decisionId': result['decisionId'],
              'trade': result.get('trade'), 'replayed': request['result']['replayed']}
    state['cycle']['actions'].append(action)
    state['cycle']['entryEligible'] = False
    if result['status'] == 'executed':
        trade = result['trade']
        telemetry = action['execution']
        telemetry['spreadCostUsd'] = str(D(trade['quantity']) * (D(telemetry['executionAsk']) - D(telemetry['executionBid'])) / 2)
        telemetry['slippageCostUsd'] = str(D(trade['quantity']) * abs(D(trade['price']) - D(telemetry['executionReferencePrice'])))
        telemetry['decisionToFillSeconds'] = datetime.fromisoformat(trade['createdAt'].replace('Z', '+00:00')).timestamp() - telemetry['decisionQuoteReceivedAt']
        state['expectedVersion'] += 1
        symbol = action['symbol']
        key = pending['budgetKey']
        state['cycle']['liquidityUsed'][key] = str(D(state['cycle']['liquidityUsed'].get(key, '0')) + D(trade['quantity']))
        if action['action'] == 'BUY':
            state['policyState']['positions'][symbol] = pending['entryMetadata']
            state['cycle']['stopAfterBuy'] = True
        elif (D(pending['quantityBefore']) - D(trade['quantity'])) * D(pending['dustMark']) < MICRO:
            # Keep metadata for residual quantity that may later rise above USD
            # precision; a future entry replaces it only after a confirmed fill.
            state['policyState']['lastExitTime'][symbol] = datetime.fromisoformat(trade['createdAt'].replace('Z', '+00:00')).timestamp()
            state['closing'] = [s for s in state['closing'] if s != symbol]
    else:
        state['cycle']['stopAfterRejection'] = True
    state['pending'] = None
    core.save_json(directory / 'state.json', state)


def finish_cycle(directory, state, context=None, status='complete'):
    cycle = deepcopy(state['cycle'])
    cycle.update(status=status, context=context)
    (directory / 'cycles').mkdir(parents=True, exist_ok=True)
    core.save_json(directory / 'cycles' / (str(cycle['minute']) + '.json'), cycle)
    state['lastMinute'] = cycle['minute']
    state['cycle'] = None
    core.save_json(directory / 'state.json', state)
    return {'status': status, 'minute': cycle['minute'], 'reason': cycle.get('reason'), 'actions': cycle['actions']}


def execute(root, mode):
    core.require(mode in MODES, 'Unknown branch')
    experiment = core.read_json(root / 'experiment.json')
    directory = root / mode
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'state.json'
    state = core.read_json(path) if path.exists() else new_state(experiment['backends'][mode])
    core.require(state['backend'] == experiment['backends'][mode], 'Backend changed')
    # Commit recovery is independent of strategy-source checks; never invent a new request key.
    recover_pending(directory, state)
    check_manifest(experiment)
    minute = int(time.time()) // 60
    if minute < experiment['startMinute']:
        return {'status': 'waiting', 'reason': 'Starts next 15-minute bar'}
    if state['cycle'] and state['cycle']['minute'] < minute:
        finish_cycle(directory, state, status='interrupted')
    if state['lastMinute'] >= minute:
        return {'status': 'skipped', 'reason': 'Minute already complete'}
    event = feed.read_event(root / 'market', minute)
    if not event:
        return {'status': 'waiting', 'reason': 'No immutable market event this minute'}
    core.require(event['minute'] == minute and event['features']['bar'] == minute // 15, 'Event cadence differs')
    core.require(0 <= time.time() - event['execution']['startedAt'] <= 45, 'Execution book expired')
    context = checked_context(state['backend'], marks(event['decision']))
    core.require(context['portfolio']['version'] == state['expectedVersion'], 'Portfolio changed outside runner')
    if not state['cycle']:
        state['cycle'] = {'minute': minute, 'mode': mode, 'marketId': event['marketId'],
                          'featureId': event['featureId'], 'version': experiment['version'], 'actions': [],
                          'liquidityUsed': {}, 'entryEligible': state['entryBarProcessed'] < event['features']['bar']}
        state['entryBarProcessed'] = event['features']['bar']
        core.save_json(path, state)
    core.require(state['cycle']['marketId'] == event['marketId'], 'Active cycle event changed')
    for _ in range(EXECUTION['maximumActionsPerMinute']):
        cycle = state['cycle']
        context = checked_context(state['backend'], marks(event['decision']))
        core.require(context['portfolio']['version'] == state['expectedVersion'], 'Unexpected portfolio version')
        if cycle.get('stopAfterBuy'):
            break
        if cycle.get('stopAfterRejection') or len(cycle['actions']) >= EXECUTION['maximumActionsPerMinute']:
            cycle['reason'] = 'ACTION_LIMIT_OR_REJECTION'
            break
        candidate, closing, gates, reason = policy.plan(context, event['features'], event['decision'], mode,
                                                      state['policyState'], state['closing'], cycle['entryEligible'],
                                                      evaluation_time=time.time())
        state['closing'] = closing
        cycle.update(gates=gates, reason=reason)
        core.save_json(path, state)
        if candidate['action'] == 'HOLD':
            break
        order, blocked = execution_order(candidate, context, event, cycle, time.time())
        if blocked:
            cycle['reason'] = blocked
            break
        symbol = candidate['symbol']
        execution_market = marks(event['execution'], (symbol, order['price']))
        fresh = checked_context(state['backend'], execution_market)
        core.require(fresh['portfolio']['version'] == state['expectedVersion'], 'Portfolio changed during execution')
        core.assert_fresh(execution_market, fresh, time.time())
        now = time.time()
        expires = min(q['receivedAt'] for q in event['execution']['symbols'].values()) + 45
        core.require(now + core.HTTP_TIMEOUT < expires, 'Not enough fresh-quote time to submit')
        payload = {key: candidate[key] for key in ('action', 'confidence', 'riskLevel', 'rationale')}
        payload.update(contextId=fresh['contextId'], strategyId='intraday-' + mode + '-v4',
                       strategyVersion=experiment['version'], source='intraday-paper-' + mode,
                       symbol=symbol, amountUsd=core.api_decimal(order['amountUsd']), price=core.api_decimal(order['price']))
        action_dir = directory / 'requests' / (str(minute) + '-' + uuid.uuid4().hex)
        action_dir.mkdir(parents=True)
        request = {'complete': False, 'backend': state['backend'], 'slot': minute, 'slotSeconds': 60,
                   'runDir': str(action_dir), 'expiresAt': expires, 'key': str(uuid.uuid4()), 'body': core.dumps(payload)}
        action = {'action': candidate['action'], 'symbol': symbol, 'amountUsd': order['amountUsd'],
                  'price': order['price'], 'execution': order['execution'], 'ruleReason': reason}
        state['pending'] = {'directory': str(action_dir), 'action': action, 'budgetKey': order['budgetKey'],
                            'quantityBefore': str(quantity(context, symbol)), 'decisionTime': event['decision']['serverTimeMs'] / 1000,
                            'dustMark': str(max(D(event['decision']['symbols'][symbol]['bid']), D(event['execution']['symbols'][symbol]['bid']))),
                            'entryMetadata': {'entryTime': now, 'entryPrice': order['price'],
                                              'stopPrice': str(D(order['price']) - 2 * D(event['features']['symbols'][symbol]['atr14_15m']))}}
        core.save_json(action_dir / 'evidence.json', {'context': context, 'executionContext': fresh, 'gates': gates,
                                                    'payload': payload, 'marketId': event['marketId'], 'featureId': event['featureId']})
        core.save_json(action_dir / 'state.json', request)
        core.save_json(path, state)
        recover_pending(directory, state)
        if candidate['action'] == 'BUY':
            break
    final = checked_context(state['backend'], marks(event['execution']))
    core.require(final['portfolio']['version'] == state['expectedVersion'], 'Final portfolio version differs')
    return finish_cycle(directory, state, final)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'paper-intraday-v4')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--mode', choices=MODES)
    parser.add_argument('--slow-backend', default='http://127.0.0.1:3006')
    parser.add_argument('--pullback-backend', default='http://127.0.0.1:3007')
    args = parser.parse_args()
    core.require(sum((args.initialize, bool(args.mode))) == 1, 'Choose initialize or mode')
    core.os.umask(0o077)
    root = args.root.resolve()
    directory = root if args.initialize else root / args.mode
    directory.mkdir(parents=True, exist_ok=True)
    with core.exclusive_lock(directory):
        result = initialize(root, {'slow': args.slow_backend, 'pullback': args.pullback_backend}) if args.initialize else execute(root, args.mode)
        print(core.dumps(result), flush=True)


if __name__ == '__main__':
    main()
