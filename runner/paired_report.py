#!/usr/bin/env python3
"""V2 coverage and paired paper performance; never submits a signal."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import time

import run as core
from market_snapshot import read_event


def summarize(root, now=None):
    now = time.time() if now is None else now
    experiment = core.read_json(root / 'experiment.json')
    # Consumer deadline is before minute 06. Missing is different from HOLD.
    last_due = int(now) // 3600 - (1 if now % 3600 < 360 else 0)
    expected = set(range(experiment['startSlot'], last_due + 1))
    available = {int(p.stem) for p in (root / 'market').glob('*.json') if p.stem.isdigit()}
    market = core.collect_market()
    accounts, completed, entries = {}, {}, {}
    for mode in ('ai', 'control'):
        context = core.fetch_context(experiment['backends'][mode], market)
        outcomes, fills, votes = Counter(), Counter(), Counter()
        seen, pending, curve, latencies = {}, [], [], []
        entries[mode] = {}
        state_path = root / mode / 'state.json'
        state = core.read_json(state_path) if state_path.exists() else None
        for path in sorted((root / mode / 'runs').glob('*/request.json')):
            request = core.read_json(path)
            evidence = core.read_json(path.parent / 'evidence.json')
            result_path = path.parent / 'result.json'
            result = core.read_json(result_path) if result_path.exists() else (
                state['result'] if state and state['complete'] and state['key'] == request['key'] else None)
            if result is None:
                pending.append({'slot': request['slot'], 'runDir': str(path.parent), 'expired': now >= request['expiresAt']})
                continue
            slot = request['slot']
            core.require(slot not in seen, 'Duplicate completed slot: ' + mode)
            event = read_event(root / 'market', slot)
            core.require(event and event['marketId'] == evidence['marketId'] == request['marketId'], 'Market audit mismatch')
            core.require(evidence['version'] == experiment['version'], 'Mixed strategy versions')
            response = result['response']
            seen[slot] = evidence['marketId']
            entries[mode][slot] = evidence
            outcomes[response['status']] += 1
            equity = D(str(evidence['context']['portfolio']['equity']))
            curve.append((slot, equity))
            vote = evidence.get('aiVote')
            if vote:
                votes['error' if vote['status'] != 'ok' else 'approved' if vote['approve'] else 'vetoed'] += 1
            if response['status'] == 'executed':
                fills[evidence['candidate']['action']] += 1
                curve.append((slot + 0.1, equity - D(str(response['trade']['feeUsd']))))
                filled = datetime.fromisoformat(response['trade']['createdAt'].replace('Z', '+00:00')).timestamp()
                latencies.append(filled - evidence['market']['startedAt'])
        peak, dd = D(50), D(0)
        curve.sort()
        curve.append((now / 3600, D(str(context['portfolio']['equity']))))
        for _, equity in curve:
            peak = max(peak, equity)
            dd = max(dd, (peak - equity) / peak * 100)
        completed[mode] = seen
        accounts[mode] = {'portfolio': context['portfolio'], 'positions': context['positions'], 'risk': context['risk'],
                          'completedSlots': len(seen), 'outcomes': dict(outcomes), 'fills': dict(fills), 'aiVotes': dict(votes),
                          'pendingRequests': pending, 'missingSlots': sorted(expected - set(seen)),
                          'returnPct': str((D(str(context['portfolio']['equity'])) / 50 - 1) * 100),
                          'observedMaxDrawdownPct': str(dd),
                          'meanQuoteToFillSeconds': sum(latencies) / len(latencies) if latencies else None}
    matched = set(completed['ai']) & set(completed['control'])
    core.require(all(completed['ai'][s] == completed['control'][s] for s in matched), 'Consumers used different market events')
    same_proposals = sum(all(entries['ai'][s]['ruleCandidate'][key] == entries['control'][s]['ruleCandidate'][key]
                             for key in ('action', 'symbol', 'amountUsd')) for s in matched)
    return {'generatedAt': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'experiment': experiment,
            'accounts': accounts, 'matchedMarketSlots': len(matched), 'sameRuleProposalSlots': same_proposals,
            'missingMarketSlots': sorted(expected - available),
            'limitations': ['Both branches use the same cached quote; latency/slippage and infrastructure costs are excluded.',
                           'Hourly and report-time drawdown only, not intrahour extremes.',
                           'After an AI veto, holdings and later eligible opportunities can differ.',
                           'AI errors, vetoes, missing slots and risk rejections are distinct; compare coverage with PnL.',
                           'V2 starts from two fresh $50 accounts; do not pool V1 history.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'paper-v2')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = summarize(args.root.resolve())
    if args.output:
        core.save_json(args.output, report)
    print(core.dumps(report))


if __name__ == '__main__':
    main()
