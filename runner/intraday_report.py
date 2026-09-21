#!/usr/bin/env python3
"""Read-only V4 forward evidence, bid valuation, accounting and coverage report."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_DOWN
import json
from pathlib import Path
import time

import intraday_market as feed
from intraday_policy import SYMBOLS

MODES = ('slow', 'pullback')
INITIAL = D('50')
MICRO = D('0.000001')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def number(value, nonnegative=False):
    require(not isinstance(value, bool), 'Boolean is not a report number')
    result = D(str(value))
    require(result.is_finite() and (not nonnegative or result >= 0), 'Invalid report number')
    return result


def validate_manifest(experiment):
    require(experiment.get('schemaVersion') == 4 and experiment.get('kind') == 'intraday-v4',
            'V4 experiment manifest required')
    require(set(experiment.get('backends', {})) == set(MODES)
            and len(set(experiment['backends'].values())) == 2, 'Two separate V4 backends required')
    require(experiment.get('symbols') == list(SYMBOLS), 'V4 five-symbol universe required')
    require(isinstance(experiment.get('version'), str) and experiment['version'], 'Pinned version required')
    require(type(experiment.get('startMinute')) is int and experiment['startMinute'] >= 0,
            'Invalid prospective start minute')
    require(type(experiment.get('startBar')) is int
            and experiment['startBar'] * 15 == experiment['startMinute'], 'Invalid start bar')


def marks(book):
    return {'symbols': {symbol: {'price': str(number(book['symbols'][symbol]['bid']).quantize(
        MICRO, rounding=ROUND_DOWN))} for symbol in SYMBOLS}}


def portfolio_values(context, book):
    portfolio = context['portfolio']
    require(number(portfolio['initialCapital']) == INITIAL, 'V4 requires fresh $50 accounts')
    positions = context['positions']
    require(len({position['symbol'] for position in positions}) == len(positions), 'Duplicate portfolio position')
    exposure = D(0)
    for position in positions:
        require(position['symbol'] in SYMBOLS, 'Position outside V4 universe')
        exposure += number(position['quantity'], True) * number(book['symbols'][position['symbol']]['bid'])
    equity = number(portfolio['cash'], True) + exposure
    return equity, exposure


def build_report(root, now=None, book=None, contexts=None, monthly_operating_cost_usd='0'):
    root = Path(root)
    now = time.time() if now is None else now
    require(number(now, True).is_finite(), 'Invalid report time')
    experiment = read_json(root / 'experiment.json')
    validate_manifest(experiment)
    monthly_cost = number(monthly_operating_cost_usd, True)
    start = experiment['startMinute']
    # A current-minute consumer may still run at :45; only previous minutes are due.
    current_minute, last_due = int(now) // 60, int(now) // 60 - 1
    expected = set(range(start, last_due + 1))
    elapsed = max(0, now - start * 60)
    operating_cost = monthly_cost * D(str(elapsed)) / D(30 * 86400)
    book = feed.collect_book() if book is None else book
    feed.validate_book(book)
    if contexts is None:
        from intraday_runner import checked_context
        contexts = {mode: checked_context(experiment['backends'][mode], marks(book)) for mode in MODES}
    events, cache, cache_bar = {}, {}, None
    for path in sorted((root / 'market').glob('*.json'), key=lambda p: int(p.stem) if p.stem.isdigit() else -1):
        if not path.stem.isdigit() or not start <= int(path.stem) <= current_minute:
            continue
        minute = int(path.stem)
        if minute // 15 != cache_bar:
            cache.clear()  # Never retain weeks of raw candle histories in RAM.
            cache_bar = minute // 15
        event = feed.read_event(root / 'market', minute, feature_cache=cache)
        require(event is not None and event['minute'] == minute, 'Missing or mismatched minute event')
        events[minute] = (event['marketId'], event['featureId'])

    accounts, completed = {}, {}
    for mode in MODES:
        context = contexts[mode]
        final_equity, final_exposure = portfolio_values(context, book)
        portfolio = context['portfolio']
        version = portfolio['version']
        require(type(version) is int and version >= 0, 'Invalid portfolio version')
        state_path = root / mode / 'state.json'
        state = read_json(state_path) if state_path.exists() else {}
        reasons, statuses, fills, failed_gates, buys = (Counter() for _ in range(5))
        turnover, fees, spread_cost, slippage_cost = (D(0) for _ in range(4))
        latencies, quantities, seen_decisions, seen_bars, open_rounds = [], {}, set(), set(), set()
        peak, drawdown, exposure_sum, mark_samples, round_trips = INITIAL, D(0), D(0), 0, 0
        seen, interrupted, cost_samples = {}, [], 0
        for path in sorted((root / mode / 'cycles').glob('*.json'), key=lambda p: int(p.stem) if p.stem.isdigit() else -1):
            require(path.stem.isdigit(), 'Unexpected cycle filename')
            cycle = read_json(path)
            minute = cycle['minute']
            require(type(minute) is int and minute == int(path.stem), 'Cycle filename/minute mismatch')
            if minute < start or minute > current_minute:
                continue
            require(cycle['mode'] == mode and cycle['version'] == experiment['version'], 'Mixed branch or strategy version')
            require(events.get(minute) == (cycle['marketId'], cycle['featureId']), 'Cycle market evidence mismatch')
            require(cycle.get('aiVote') is None, 'V4 must not call AI per decision')
            # A report is a snapshot; a concurrently completed future ledger version belongs to the next one.
            final_context = cycle['context']
            if final_context is not None and final_context['portfolio']['version'] > version:
                continue
            if cycle['status'] == 'complete':
                require(final_context is not None, 'Completed cycle requires final context')
                require(minute not in seen, 'Duplicate completed minute')
                seen[minute] = cycle['marketId']
                equity = number(final_context['portfolio']['equity'])
                exposure = sum(number(p['marketValue'], True) for p in final_context['positions'])
                peak = max(peak, equity)
                drawdown = max(drawdown, (peak - equity) / peak * 100)
                exposure_sum += exposure
                mark_samples += 1
            else:
                require(cycle['status'] == 'interrupted', 'Unknown cycle status')
                interrupted.append(minute)
            reasons[cycle.get('reason', 'INTERRUPTED_BEFORE_POLICY')] += 1
            bar = minute // 15
            # Count entry gates once per feature bar, not 15 repeated minute risk observations.
            if bar not in seen_bars:
                seen_bars.add(bar)
                for symbol, gates in cycle.get('gates', {}).items():
                    for name in gates.get('failedGates', []):
                        failed_gates[symbol + ':' + name] += 1
            for action in cycle['actions']:
                status = action['status']
                require(status in ('executed', 'held', 'rejected'), 'Invalid action status')
                statuses[status] += 1
                decision = action.get('decisionId')
                if decision is not None:
                    require(decision not in seen_decisions, 'Duplicate decision in cycle history')
                    seen_decisions.add(decision)
                if status != 'executed':
                    continue
                side, symbol, trade = action['action'], action['symbol'], action['trade']
                require(side in ('BUY', 'SELL') and symbol in SYMBOLS, 'Invalid filled action')
                require(trade['side'] == side and trade['symbol'] == symbol, 'Trade/action mismatch')
                gross, fee, quantity = (number(trade[k], True) for k in ('grossUsd', 'feeUsd', 'quantity'))
                require(gross > 0 and quantity > 0, 'Empty fill')
                turnover += gross
                fees += fee
                fills[side] += 1
                if side == 'BUY':
                    buys[symbol] += 1
                    open_rounds.add(symbol)
                    quantities[symbol] = quantities.get(symbol, D(0)) + quantity
                else:
                    remaining = quantities.get(symbol, D(0)) - quantity
                    require(remaining >= 0, 'Sell exceeds logged filled quantity')
                    quantities[symbol] = remaining
                    if symbol in open_rounds and remaining * number(trade['price']) < MICRO:
                        round_trips += 1
                        open_rounds.remove(symbol)
                telemetry = action.get('execution', {})
                # These costs are already embedded in fill prices; never subtract them again.
                if all(k in telemetry for k in ('executionAsk', 'executionBid', 'executionReferencePrice')):
                    spread_cost += quantity * (number(telemetry['executionAsk']) - number(telemetry['executionBid'])) / 2
                    slippage_cost += quantity * abs(number(trade['price']) - number(telemetry['executionReferencePrice']))
                    cost_samples += 1
                if telemetry.get('decisionToFillSeconds') is not None:
                    latencies.append(float(number(telemetry['decisionToFillSeconds'], True)))
                elif telemetry.get('decisionQuoteReceivedAt') is not None:
                    filled_at = datetime.fromisoformat(trade['createdAt'].replace('Z', '+00:00')).timestamp()
                    latencies.append(float(number(filled_at - telemetry['decisionQuoteReceivedAt'], True)))
        peak = max(peak, final_equity)
        drawdown = max(drawdown, (peak - final_equity) / peak * 100)
        ledger_fees = number(portfolio['totalFees'], True)
        pending = state.get('pending')
        pending_minute = (state.get('cycle') or {}).get('minute') if pending else None
        pending_minutes = {pending_minute} if type(pending_minute) is int else set()
        completed[mode] = seen
        scenarios = {}
        for bps in (0, 5, 10):
            extra = turnover * D(bps) / 10000
            adjusted = final_equity - extra - operating_cost
            scenarios[str(bps)] = {'additionalExecutionCostUsd': str(extra),
                'assumedOperatingCostUsd': str(operating_cost), 'netEquityUsd': str(adjusted),
                'returnPct': str((adjusted / INITIAL - 1) * 100)}
        accounts[mode] = {
            'portfolio': portfolio, 'positions': context['positions'], 'risk': context['risk'],
            'portfolioVersionAtValuation': version, 'completedMinutes': len(seen),
            'missingMinutes': sorted(expected - set(seen) - pending_minutes),
            'incompleteMinutes': sorted(expected - set(seen)), 'pending': pending,
            'interruptedMinutes': interrupted,
            'lastMinute': state.get('lastMinute'), 'closing': state.get('closing', []),
            'policyState': state.get('policyState', {}), 'actionOutcomes': dict(statuses),
            'fills': dict(fills), 'completedRoundTrips': round_trips,
            'buysBySymbol': dict(buys), 'minuteRuleReasons': dict(reasons),
            'observedEntryFeatureBars': len(seen_bars), 'failedEntryGatesBySymbolPerBar': dict(failed_gates),
            'netEquityUsd': str(final_equity), 'netPnlUsd': str(final_equity - INITIAL),
            'returnPct': str((final_equity / INITIAL - 1) * 100),
            'currentExposureUsd': str(final_exposure), 'observedMaxDrawdownPct': str(drawdown),
            'meanSampledExposureUsd': str(exposure_sum / mark_samples) if mark_samples else None,
            'feesPaidUsd': str(ledger_fees), 'loggedFeesUsd': str(fees),
            'feeReconciliationDifferenceUsd': str(ledger_fees - fees),
            'grossTurnoverUsd': str(turnover), 'executionCostTelemetrySamples': cost_samples,
            'reportedEmbeddedSpreadCostUsd': str(spread_cost) if cost_samples else None,
            'reportedEmbeddedSlippageCostUsd': str(slippage_cost) if cost_samples else None,
            'meanDecisionToFillSeconds': sum(latencies) / len(latencies) if latencies else None,
            'costAdjustedScenarios': scenarios,
        }
    matched = set(completed['slow']) & set(completed['pullback'])
    require(all(completed['slow'][minute] == completed['pullback'][minute] for minute in matched),
            'Comparison branches used different market events')
    return {
        'generatedAt': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'experiment': experiment,
        'valuation': {'basis': 'Fresh best bids, before hypothetical exit fees/slippage; actual paper fees already included.',
            'bookStartedAt': book['startedAt'], 'bookCompletedAt': book['completedAt'],
            'localReceiveTimesAreNotExchangeTimes': True},
        'elapsedForwardDays': elapsed / 86400, 'accounts': accounts,
        'matchedMarketMinutes': len(matched), 'dueMinutes': len(expected),
        'missingMarketMinutes': sorted(expected - set(events)),
        'operatingCost': {'status': 'not_measured' if monthly_cost == 0 else 'assumption_not_measured',
            'monthlyUsdPerBranch': str(monthly_cost), 'assumedCostUsdPerBranch': str(operating_cost)},
        'review': {'earliestReviewAt': datetime.fromtimestamp(start * 60 + 14 * 86400, timezone.utc).isoformat(),
            'fourteenDaysElapsed': elapsed >= 14 * 86400, 'automaticPromotion': False,
            'status': 'manual_review_required' if elapsed >= 14 * 86400 else 'collecting_forward_evidence',
            'reason': 'Fourteen days is a first review date, not sufficient proof of profitability or permission for live trading.'},
        'limitations': [
            'Both branches use common V4 execution, sizing and exits; slow retains the V3 entry hypothesis only. Original V3 remains separate.',
            'BUY ask plus 2bps and SELL bid minus 2bps are modeled fills. Displayed top quantity caps do not model queue priority, cancellations or market impact.',
            'The shared delayed quote is observed public data; collection and HTTP fill latency are local measurements, not exchange execution latency.',
            'Ledger fees and modeled execution costs are already in net equity. Additional 5/10bps scenarios are first-order turnover charges, not strategy replays.',
            'Operating costs are not measured when zero. Return is based on the full $50 account, including idle cash.',
            'Drawdown uses completed minute bid marks and report-time equity; between-sample extremes and missing minutes are unobserved.',
            'A nonzero fee difference can identify committed but unfinished pending cycles or missing evidence; reconcile the ledger before comparing outcomes.',
            'Closed 15m/4h features and parameters are predeclared hypotheses; no tuning on observed forward results or automatic live promotion.',
            'Paper $1 minimum does not establish live exchange order-filter compatibility. AI remains an offline research/review tool.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'paper-intraday-v4')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--monthly-operating-cost-usd', default='0')
    args = parser.parse_args()
    report = build_report(args.root.resolve(), monthly_operating_cost_usd=args.monthly_operating_cost_usd)
    import run as core
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        core.save_json(args.output, report)
    print(core.dumps(report), flush=True)


if __name__ == '__main__':
    main()
