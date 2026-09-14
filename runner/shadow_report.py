#!/usr/bin/env python3
"""Read paper accounts and local evidence; never submits an order."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import time

import run as core


def summarize(directory, now=None):
    now = time.time() if now is None else now
    experiment = core.read_json(directory / 'experiment.json')
    market = core.collect_market()
    accounts = {}
    for name, backend in (('ai', experiment['baseline']), ('control', experiment['backend'])):
        context = core.fetch_context(backend, market)
        accounts[name] = {'asOf': context['asOf'], 'portfolio': context['portfolio'], 'positions': context['positions']}
    observed = {}
    pending = []
    curve = []
    agreements = Counter()
    buy_opportunities = Counter()
    fills = Counter()
    latencies = []
    for path in sorted((directory / 'runs').glob('*/request.json')):
        request = core.read_json(path)
        evidence = core.read_json(path.parent / 'evidence.json')
        result_path = path.parent / 'result.json'
        if not result_path.exists():
            pending.append({'slot': request['slot'], 'runDir': str(path.parent)})
            continue
        result = core.read_json(result_path)
        slot = request['slot']
        core.require(slot not in observed, 'Duplicate completed control slot in evidence')
        observed[slot] = result['response']['status']
        candidate, comparison = evidence['candidate'], evidence['comparison']
        agreements[comparison['classification']] += 1
        if candidate['action'] == 'BUY':
            buy_opportunities[comparison['classification']] += 1
        if result['response']['status'] == 'executed':
            fills[candidate['action']] += 1
        # Pre-decision equity and post-fill fee dip at the same marks.
        equity = D(str(evidence['context']['portfolio']['equity']))
        curve.append((slot, equity))
        if result['response']['status'] == 'executed':
            fee = D(str(result['response']['trade']['feeUsd']))
            curve.append((slot + 0.1, equity - fee))
            latencies.append(datetime.fromisoformat(result['response']['trade']['createdAt'].replace('Z', '+00:00')).timestamp()
                             - evidence['market']['startedAt'])
    # The last attempt is at minute 05:30; do not call the current hour missing before then.
    last_due = int(now) // 3600 - (1 if now % 3600 < 360 else 0)
    missing = [s for s in range(experiment['startSlot'], last_due + 1) if s not in observed]
    peak, drawdown = D('50'), D(0)
    curve.sort(key=lambda pair: pair[0])
    curve.append((now / 3600, D(str(accounts['control']['portfolio']['equity']))))
    for _, equity in curve:
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)
    return {'generatedAt': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'experiment': experiment,
            'accounts': accounts, 'completedControlSlots': len(observed), 'outcomes': dict(Counter(observed.values())),
            'controlFills': dict(fills), 'actionAgreement': dict(agreements),
            'controlBuyOpportunityAgreement': dict(buy_opportunities), 'pendingRequests': pending,
            'missingSlotsUtc': [datetime.fromtimestamp(s * 3600, timezone.utc).isoformat() for s in missing],
            'controlObservedMaxDrawdownPct': str(drawdown),
            'controlMeanQuoteToFillSeconds': sum(latencies) / len(latencies) if latencies else None,
            'limitations': ['AI acts on its own portfolio; action agreement is observational, not causal attribution.',
                           'Control uses the identical input quote later than AI; fills are simulated without spread/slippage.',
                           'Missing/failed AI slots cannot be traded by this matched-input control.',
                           'Drawdown uses observed hourly marks and current marks, not intrahour extremes.',
                           'Paper returns exclude AI/VPS costs. No automatic strategy promotion.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path.home() / 'paper-shadow' / 'state')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    directory = args.state_dir.resolve()
    with core.exclusive_lock(directory):
        # Recovery may commit state.json immediately before writing result.json.
        state_path = directory / 'state.json'
        if state_path.exists():
            state = core.read_json(state_path)
            result_path = Path(state['runDir']) / 'result.json'
            if state['complete'] and not result_path.exists():
                core.save_json(result_path, state['result'])
        report = summarize(directory)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            core.save_json(args.output, report)
        print(core.dumps(report), flush=True)


if __name__ == '__main__':
    main()
