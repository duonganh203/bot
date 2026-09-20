#!/usr/bin/env python3
"""Read-only V3 forward paper report, including explicit cost and benchmark limits."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import time

import run as core
import research_market as feed


MODES = ('baseline', 'candidate')
INITIAL_CAPITAL = D('50')
BENCHMARK_NOTIONAL = D('20')
FEE_RATE = D('0.001')


def decimal(value, name, nonnegative=False):
    number = D(str(value))
    core.require(number.is_finite() and (not nonnegative or number >= 0), 'Invalid ' + name)
    return number


def validate_manifest(experiment):
    core.require(experiment.get('schemaVersion') == 3 and experiment.get('kind') == 'research-v3',
                 'Research V3 manifest required')
    core.require(set(experiment.get('backends', {})) == set(MODES)
                 and len(set(experiment['backends'].values())) == 2, 'Separate V3 backends required')
    core.require(experiment.get('symbols') == list(core.SUPPORTED_SYMBOLS), 'V3 five-symbol universe required')
    core.require(isinstance(experiment.get('version'), str) and bool(experiment['version']),
                 'Pinned experiment version required')
    core.require(type(experiment.get('startSlot')) is int and experiment['startSlot'] >= 0,
                 'Invalid experiment start slot')


def benchmarks(experiment, events, current_market):
    """Never use initialization/preflight prices or silently move the start after a gap."""
    slot = experiment['startSlot']
    cash = {'status': 'available', 'netEquityUsd': '50', 'netPnlUsd': '0', 'returnPct': '0',
            'entryFeeUsd': '0', 'initialInvestedNotionalUsd': '0', 'finalExposureUsd': '0'}
    result = {'cash': cash, 'startSlot': slot, 'startMarketId': None,
              'valuation': 'Mark to current shared quotes; 0.1% entry fee, no exit fee, no spread/slippage or operating costs.'}
    start = events.get(slot)
    if start is None:
        for name in ('btc20_cash', 'equal_weight_five20_cash'):
            result[name] = {'status': 'unavailable', 'reason': 'First scheduled market event is unavailable; start is not shifted.'}
        return result
    result['startMarketId'] = start['marketId']
    result['startedAt'] = start['market']['startedAt']
    for name, symbols in (('btc20_cash', ('BTCUSDT',)),
                          ('equal_weight_five20_cash', tuple(experiment['symbols']))):
        notional = BENCHMARK_NOTIONAL / len(symbols)
        quantities = {s: notional / core.positive(start['market']['symbols'][s]['price']) for s in symbols}
        exposure = sum(quantities[s] * core.positive(current_market['symbols'][s]['price']) for s in symbols)
        fee = BENCHMARK_NOTIONAL * FEE_RATE
        remaining_cash = INITIAL_CAPITAL - BENCHMARK_NOTIONAL - fee
        equity = remaining_cash + exposure
        result[name] = {'status': 'available', 'netEquityUsd': str(equity),
                        'netPnlUsd': str(equity - INITIAL_CAPITAL),
                        'returnPct': str((equity / INITIAL_CAPITAL - 1) * 100),
                        'entryFeeUsd': str(fee), 'cashUsd': str(remaining_cash),
                        'initialInvestedNotionalUsd': str(BENCHMARK_NOTIONAL),
                        'finalExposureUsd': str(exposure),
                        'quantities': {s: str(q) for s, q in quantities.items()}}
    return result


def measured_spreads(events):
    """Descriptive quotes only: a spread observation is not a simulated execution."""
    samples = {s: [] for s in core.SUPPORTED_SYMBOLS}
    for event in events.values():
        for symbol, quote in event['market']['symbols'].items():
            if 'bookBidPrice' in quote and 'bookAskPrice' in quote:
                bid, ask = core.positive(quote['bookBidPrice']), core.positive(quote['bookAskPrice'])
                core.require(ask >= bid, 'Crossed observed book')
                samples[symbol].append((ask - bid) / ((ask + bid) / 2) * 10000)
    return {'basis': 'Full best ask-minus-bid spread / midpoint, in basis points; observations only, not deducted from PnL.',
            'bySymbol': {s: {'samples': len(values), 'meanBps': str(sum(values) / len(values)) if values else None,
                             'maxBps': str(max(values)) if values else None} for s, values in samples.items()}}


def summarize(root, now=None, monthly_operating_cost_usd='0', market=None, contexts=None):
    root = Path(root)
    now = time.time() if now is None else now
    experiment = core.read_json(root / 'experiment.json')
    validate_manifest(experiment)
    monthly_cost = decimal(monthly_operating_cost_usd, 'monthly operating cost', True)
    last_due = int(now) // 3600 - (1 if now % 3600 < 360 else 0)
    expected = set(range(experiment['startSlot'], last_due + 1))
    events = {}
    for path in (root / 'market').glob('*.json'):
        if path.stem.isdigit() and experiment['startSlot'] <= int(path.stem) <= int(now) // 3600:
            slot = int(path.stem)
            event = feed.read_event(root / 'market', slot)
            core.require(set(event['market']['symbols']) == set(experiment['symbols']), 'Market universe mismatch')
            events[slot] = event
    market = feed.collect_market(experiment['symbols']) if market is None else market
    core.require(set(market['symbols']) == set(experiment['symbols']), 'Current valuation universe mismatch')
    elapsed = max(0, now - experiment['startSlot'] * 3600)
    operating_cost = monthly_cost * D(str(elapsed)) / D(30 * 86400)
    accounts, completed, entries = {}, {}, {}
    for mode in MODES:
        context = core.fetch_context(experiment['backends'][mode], market) if contexts is None else contexts[mode]
        portfolio = context['portfolio']
        core.require(decimal(portfolio['initialCapital'], 'initial capital') == INITIAL_CAPITAL,
                     'V3 requires fresh $50 accounts')
        outcomes, fills, reasons, buys = Counter(), Counter(), Counter(), Counter()
        exposures, curve, latencies, pending = [], [], [], []
        turnover, logged_fees = D(0), D(0)
        seen, requested = {}, set()
        entries[mode] = {}
        state_path = root / mode / 'state.json'
        state = core.read_json(state_path) if state_path.exists() else None
        for path in sorted((root / mode / 'runs').glob('*/request.json')):
            request = core.read_json(path)
            slot = request['slot']
            if slot < experiment['startSlot']:
                continue  # Preflight has a separate history and is never prospective evidence.
            core.require(type(slot) is int and slot <= int(now) // 3600, 'Invalid/future request slot')
            core.require(slot not in requested, 'Duplicate request slot: ' + mode)
            requested.add(slot)
            evidence = core.read_json(path.parent / 'evidence.json')
            event = events.get(slot)
            core.require(event and event['marketId'] == evidence['marketId'] == request['marketId'],
                         'Market audit mismatch')
            core.require(evidence['version'] == experiment['version'], 'Mixed strategy versions')
            core.require(evidence['slot'] == slot and evidence['mode'] == mode, 'Evidence branch/slot mismatch')
            core.require(evidence.get('aiVote') is None, 'V3 must not call AI')
            core.require(evidence['market'] == event['market'], 'Evidence market differs from immutable event')
            result_path = path.parent / 'result.json'
            result = core.read_json(result_path) if result_path.exists() else (
                state.get('result') if state and state.get('complete') and state.get('key') == request['key'] else None)
            if result is None:
                pending.append({'slot': slot, 'runDir': str(path.parent), 'expired': now >= request['expiresAt']})
                continue
            response = result['response']
            status = response['status']
            core.require(status in ('executed', 'held', 'rejected'), 'Unknown paper response status')
            seen[slot] = evidence['marketId']
            entries[mode][slot] = evidence
            outcomes[status] += 1
            reasons[evidence.get('ruleReason', 'UNKNOWN')] += 1
            exposures.append(sum(decimal(g['holdingUsd'], 'sampled exposure', True) for g in evidence['gates'].values()))
            equity = decimal(evidence['context']['portfolio']['equity'], 'sampled equity')
            curve.append((slot, equity))
            if status == 'executed':
                action = evidence['candidate']['action']
                core.require(action in ('BUY', 'SELL'), 'HOLD cannot execute')
                trade = response['trade']
                gross = core.positive(trade['grossUsd'])
                fee = decimal(trade['feeUsd'], 'fill fee', True)
                fills[action] += 1
                turnover += gross
                logged_fees += fee
                if action == 'BUY':
                    buys[evidence['candidate']['symbol']] += 1
                curve.append((slot + 0.1, equity - fee))
                filled = datetime.fromisoformat(trade['createdAt'].replace('Z', '+00:00')).timestamp()
                latencies.append(filled - evidence['market']['startedAt'])
        final_equity = decimal(portfolio['equity'], 'current equity')
        total_fees = decimal(portfolio['totalFees'], 'ledger total fees', True)
        peak, drawdown = INITIAL_CAPITAL, D(0)
        curve.sort()
        curve.append((now / 3600, final_equity))
        for _, equity in curve:
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak * 100)
        completed[mode] = seen
        pending_slots = {p['slot'] for p in pending}
        scenarios = {}
        for bps in (0, 5, 10):
            execution_cost = turnover * D(bps) / 10000
            adjusted = final_equity - execution_cost - operating_cost
            scenarios[str(bps)] = {'extraExecutionCostUsd': str(execution_cost),
                                    'assumedOperatingCostUsd': str(operating_cost),
                                    'netEquityUsd': str(adjusted),
                                    'returnPct': str((adjusted / INITIAL_CAPITAL - 1) * 100)}
        accounts[mode] = {'portfolio': portfolio, 'positions': context['positions'], 'risk': context['risk'],
            'completedSlots': len(seen), 'outcomes': dict(outcomes), 'fills': dict(fills), 'aiVotes': {},
            'rejectedRequests': outcomes['rejected'], 'pendingRequests': pending,
            'missingSlots': sorted(expected - set(seen) - pending_slots),
            'incompleteSlots': sorted(expected - set(seen)),
            'netEquityUsd': str(final_equity), 'netPnlUsd': str(final_equity - INITIAL_CAPITAL),
            'returnPct': str((final_equity / INITIAL_CAPITAL - 1) * 100),
            'observedMaxDrawdownPct': str(drawdown), 'feesPaidUsd': str(total_fees),
            'loggedFeesUsd': str(logged_fees), 'feeReconciliationDifferenceUsd': str(total_fees - logged_fees),
            'meanQuoteToFillSeconds': sum(latencies) / len(latencies) if latencies else None,
            'buysBySymbol': dict(buys), 'ruleReasons': dict(reasons), 'grossTurnoverUsd': str(turnover),
            'meanSampledExposureUsd': str(sum(exposures) / len(exposures)) if exposures else None,
            'meanSampledExposurePctOfInitialCapital': str(sum(exposures) / len(exposures) / INITIAL_CAPITAL * 100) if exposures else None,
            'extraExecutionCostEstimateUsd': {str(bps): str(turnover * D(bps) / 10000) for bps in (5, 10)},
            'costAdjustedScenarios': scenarios}
    matched = set(completed['baseline']) & set(completed['candidate'])
    core.require(all(completed['baseline'][s] == completed['candidate'][s] for s in matched),
                 'Branches used different market events')
    same_proposals = sum(all(entries['baseline'][s]['ruleCandidate'][key] == entries['candidate'][s]['ruleCandidate'][key]
                             for key in ('action', 'symbol', 'amountUsd')) for s in matched)
    return {'generatedAt': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'experiment': experiment,
        'valuationMarketStartedAt': market['startedAt'], 'elapsedForwardDays': elapsed / 86400,
        'accounts': accounts, 'matchedMarketSlots': len(matched), 'sameRuleProposalSlots': same_proposals,
        'missingMarketSlots': sorted(expected - set(events)), 'benchmarks': benchmarks(experiment, events, market),
        'measuredBookSpread': measured_spreads(events),
        'operatingCost': {'status': 'not_measured' if monthly_cost == 0 else 'assumption_not_measured',
                          'monthlyUsdPerBranch': str(monthly_cost), 'prorationDaysPerMonth': 30,
                          'assumedCostUsdPerBranch': str(operating_cost)},
        'promotion': {'eligible': False, 'status': 'insufficient_forward_evidence',
                      'reason': 'No automatic promotion. A manual review of new prospective evidence is required.'},
        'limitations': [
            'Paper net equity already includes ledger trading fees; it excludes real spread, slippage and operating costs.',
            '5/10 bps scenarios add costs on executed gross turnover only; first-order estimates, not strategy replays.',
            'Observed bid/ask spreads are descriptive and do not measure realized execution costs or depth.',
            'Operating cost is an explicit per-branch scenario over a 30-day month; zero means not measured, not free.',
            'Drawdown and mean exposure use available pre-decision hourly marks and report-time equity; missing marks and intrahour extremes are unobserved.',
            'Passive benchmarks invest $20 once, pay 0.1% entry fees and mark open positions without exit fees; they are not exposure-matched or rebalanced.',
            'The benchmark start is the first scheduled shared market event; preflight quotes are excluded and a missing start is never silently shifted.',
            'Historical data inspected during development is not an untouched out-of-sample test; evaluate this frozen version prospectively.',
            'Strategy changes are bundled; this experiment cannot attribute gains separately to slower trend, ranking, sizing or cooldown.',
            'No AI calls or event-information alpha are deployed in V3; no automatic real-money promotion.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'paper-research-v3')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--monthly-operating-cost-usd', default='0', help='Assumed USD per branch per 30-day month; 0 means not measured.')
    args = parser.parse_args()
    report = summarize(args.root.resolve(), monthly_operating_cost_usd=args.monthly_operating_cost_usd)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        core.save_json(args.output, report)
    print(core.dumps(report), flush=True)


if __name__ == '__main__':
    main()
