"""Isolated shared-policy research. No portfolio API, AI invocation or deployment."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_DOWN, getcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
getcontext().prec = 48
HOUR = 3600000
MICRO = D('0.000001')
Q = D('1e-24')
FEE = D('0.001')
OUT = ROOT / 'data/quant/research-20260920'

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

policy = module('shared_policy', ROOT / 'runner/policy.py')
UNIVERSES = {'two': policy.UNIVERSES['two'], 'five': policy.UNIVERSES['five'], 'five_cap10': policy.UNIVERSES['five']}
VARIANTS = ('baseline', 'batch_exit', 'peak_guard', 'batch_peak', 'passive10', 'cash')

def iso(stamp):
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat().replace('+00:00', 'Z')

def ms(date):
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp() * 1000)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

class Ledger:
    def __init__(self, symbols):
        self.symbols = symbols
        self.cash = D(50)
        self.realized = self.fees = D(0)
        self.pos = {s: {'qty': D(0), 'cost': D(0), 'fees': D(0)} for s in symbols}
        self.rounds, self.trades, self.open_round = [], [], {}
        self.day, self.daily_pnl = None, D(0)

    def roll_day(self, t):
        if self.day != t // (24 * HOUR):
            self.day, self.daily_pnl = t // (24 * HOUR), D(0)

    def exposure(self, marks):
        return sum((p['qty'] * marks[s] for s, p in self.pos.items()), D(0))

    def equity(self, marks):
        return self.cash + self.exposure(marks)

    def fill(self, action, symbol, amount, price, t):
        self.roll_day(t)
        p = self.pos[symbol]
        gross = D(amount)
        if action == 'SELL':
            gross = min(gross, p['qty'] * price).quantize(MICRO, rounding=ROUND_DOWN)
        if gross < MICRO:
            return False
        assert price > 0 and D(0) < gross <= 5
        qty = (gross / price).quantize(Q, rounding=ROUND_DOWN)
        fee, pnl = gross * FEE, D(0)
        if action == 'BUY':
            assert self.cash >= gross + fee
            self.cash -= gross + fee
            p['qty'] += qty
            p['cost'] += gross
            p['fees'] += fee
            self.open_round[symbol] = {'entry': t, 'pnl': D(0)}
        else:
            assert 0 < qty <= p['qty']
            fraction = qty / p['qty']
            cost, entry_fee = p['cost'] * fraction, p['fees'] * fraction
            pnl = gross - fee - cost - entry_fee
            self.cash += gross - fee
            p['qty'] -= qty
            p['cost'] -= cost
            p['fees'] -= entry_fee
            self.realized += pnl
            self.daily_pnl += pnl
            if symbol in self.open_round:
                cycle = self.open_round[symbol]
                cycle['pnl'] += pnl
                if p['qty'] * price < MICRO:
                    self.rounds.append({'symbol': symbol, 'entry': iso(cycle['entry']), 'exit': iso(t),
                                        'pnl': float(cycle['pnl']), 'hours': (t - cycle['entry']) / HOUR})
                    del self.open_round[symbol]
        self.fees += fee
        self.trades.append({'time': iso(t), 'action': action, 'symbol': symbol, 'price': str(price),
                            'amountUsd': str(gross), 'feeUsd': str(fee), 'realizedPnl': str(pnl)})
        assert self.cash >= 0
        return True

    def reconcile(self, marks):
        basis = sum((p['cost'] + p['fees'] for p in self.pos.values()), D(0))
        unrealized = self.exposure(marks) - basis
        error = self.equity(marks) - (50 + self.realized + unrealized)
        assert abs(error) < D('1e-35'), error
        return unrealized

def load_inputs():
    result = {}
    for symbol in policy.UNIVERSES['five']:
        paths = list((OUT / 'candles').glob(symbol + '-1h-*.json'))
        if len(paths) != 1:
            raise ValueError('Require one canonical cache for ' + symbol)
        data = json.loads(paths[0].read_text(encoding='utf-8-sig'))
        assert data['symbol'] == symbol and data.get('interval', '1h') == '1h', 'Cache symbol/interval mismatch'
        rows = data['rows']
        expected = list(range(data['start'], data['endExclusive'], HOUR))
        assert [int(r[0]) for r in rows] == expected
        closes = [D(r[4]) for r in rows]
        bars = []
        for i, row in enumerate(rows):
            assert int(row[6]) == int(row[0]) + HOUR - 1
            o, h, low, c = map(D, row[1:5])
            volume = D(row[5])
            assert all(v.is_finite() for v in (o,h,low,c,volume)), 'Non-finite OHLCV'
            assert D(0) < low <= min(o, c) <= max(o, c) <= h and volume >= 0
            features = None if i < 50 else {
                'sma20': str(sum(closes[i-20:i]) / 20),
                'sma50': str(sum(closes[i-50:i]) / 50),
                'return24hPct': str((closes[i-1] / closes[i-25] - 1) * 100)}
            bars.append({'time': int(row[0]), 'open': o, 'close': c, 'features': features})
        result[symbol] = bars
    assert all([b['time'] for b in bars] == [b['time'] for b in result['BTCUSDT']] for bars in result.values())
    return result

def simulate(data, universe, variant, start, end, bps=5):
    symbols = UNIVERSES[universe]
    ledger = Ledger(symbols)
    slip = D(bps) / 10000
    cap = D(10 if universe == 'five_cap10' else 20)
    batched = variant in ('batch_exit', 'batch_peak')
    trailing = variant in ('peak_guard', 'batch_peak')
    closing, queued_since, waits = [], {}, []
    daily, curve, reasons = {}, [], Counter()
    peak, dd, first_halt = D(50), D(0), None
    guard_latched = False
    blocked, equity_blocked, guard_blocked, peak_fee_blocked = 0, 0, 0, 0
    backlog_hours, max_backlog, exposure_sum = 0, 0, D(0)
    passive_bought = set()
    def mark(value):
        nonlocal peak, dd
        peak = max(peak, value)
        dd = max(dd, (peak-value)/peak*100)
    indices = [i for i,b in enumerate(data[symbols[0]]) if start <= b['time'] < end]
    assert indices and indices[0] >= 50
    for i in indices:
        t = data[symbols[0]][i]['time']
        ledger.roll_day(t)
        opens = {s: data[s][i]['open'] for s in symbols}
        mark(ledger.equity(opens))
        if trailing and ledger.equity(opens) <= peak * D('0.94'):
            guard_latched = True
        equity_hit = ledger.equity(opens)-D('0.005') <= 47
        daily_hit = ledger.daily_pnl <= -3
        peak_fee_hit = trailing and ledger.equity(opens)-D('0.005') <= peak*D('0.94')
        if variant not in ('cash', 'passive10'):
            equity_blocked += equity_hit
            guard_blocked += guard_latched
            peak_fee_blocked += peak_fee_hit and not guard_latched
            blocked += equity_hit or daily_hit or guard_latched or peak_fee_hit
            if (equity_hit or guard_latched or peak_fee_hit) and first_halt is None:
                first_halt = iso(t)
        market = {'symbols': {s: {'price': str(opens[s]), **data[s][i]['features']} for s in symbols}}
        def decide():
            context = {'positions': [{'symbol': s,'quantity': str(p['qty'])} for s,p in ledger.pos.items() if p['qty']],
                       'portfolio': {'cash': str(ledger.cash), 'initialCapital': 50},
                       'risk': {'dailyLossLimitReached': ledger.daily_pnl <= -3}}
            candidate, remaining, gates, reason = policy.plan(context, market, closing, symbols)
            if guard_latched:
                remaining = [s for s in symbols if ledger.pos[s]['qty'] * opens[s] >= MICRO]
                candidate = policy.hold('PEAK_GUARD')
                reason = 'PEAK_GUARD'
                if remaining:
                    s = remaining[0]
                    candidate.update(action='SELL', symbol=s, amountUsd=str(min(D(5),ledger.pos[s]['qty']*opens[s]).quantize(MICRO,rounding=ROUND_DOWN)))
            elif candidate['action'] == 'BUY' and trailing and ledger.equity(opens)-D('0.005') <= peak*D('0.94'):
                candidate, reason = policy.hold('PEAK_ENTRY_FLOOR'), 'PEAK_ENTRY_FLOOR'
            elif candidate['action'] == 'BUY' and ledger.exposure(opens)+5 > cap:
                candidate, reason = policy.hold('CAPACITY_CONTROL'), 'CAPACITY_CONTROL'
            return candidate, remaining, gates, reason
        if variant == 'cash':
            candidate, closing, gates, reason = policy.hold('CASH'), [], {}, 'CASH'
        elif variant == 'passive10':
            todo = [s for s in symbols if s not in passive_bought]
            candidate, closing, gates, reason = policy.hold('PASSIVE_HOLD'), [], {}, 'PASSIVE_HOLD'
            if todo:
                candidate.update(action='BUY', symbol=todo[0], amountUsd=str(D(10)/len(symbols)))
                passive_bought.add(todo[0])
                reason = 'PASSIVE_ENTRY'
        else:
            candidate, closing, gates, reason = decide()
        reasons[reason] += 1
        # A dust position can fall below the policy threshold without another fill.
        # Complete its old queue before a subsequent BUY creates a new position.
        for symbol in list(queued_since):
            if symbol not in closing or ledger.pos[symbol]['qty'] * opens[symbol] < MICRO:
                waits.append((t-queued_since.pop(symbol))/HOUR)
        for s in closing:
            queued_since.setdefault(s, t)
        count = 0
        while candidate['action'] != 'HOLD':
            action, s = candidate['action'], candidate['symbol']
            price = opens[s] * (1 + slip if action == 'BUY' else 1-slip)
            if not ledger.fill(action, s, candidate['amountUsd'], price, t):
                break
            count += 1
            mark(ledger.equity(opens))
            if s in queued_since and ledger.pos[s]['qty'] * opens[s] < MICRO:
                waits.append((t-queued_since.pop(s))/HOUR)
            if not batched or action != 'SELL':
                break
            candidate, closing, _, _ = decide()
            if candidate['action'] != 'SELL':
                break  # Do not add a BUY after a batch exit in the same hour.
            assert count < 100, 'Non-terminating exit batch'
        still = [s for s in queued_since if ledger.pos[s]['qty'] * opens[s] >= MICRO]
        backlog_hours += bool(still)
        max_backlog = max(max_backlog, len(still))
        marks = {s: data[s][i]['close'] for s in symbols}
        equity, exposure = ledger.equity(marks), ledger.exposure(marks)
        mark(equity)
        ledger.reconcile(marks)
        exposure_sum += exposure
        curve.append({'time': iso(t+HOUR),'equity':float(equity),'exposure':float(exposure)})
        daily[t//(24*HOUR)] = float(equity)
    pnl = [r['pnl'] for r in ledger.rounds]
    wins, losses = sum(v for v in pnl if v>0), -sum(v for v in pnl if v<0)
    daily_values = [50, *daily.values()]
    returns = [b/a-1 for a,b in zip(daily_values,daily_values[1:])]
    summary = {'universe':universe,'variant':variant,'start':iso(start),'endExclusive':iso(end),'bps':bps,
        'returnPct':float((equity/50-1)*100),'endEquity':float(equity),
        'estimatedNetExitEquity':float(ledger.cash+exposure*(1-slip)*(1-FEE)),
        'maxDrawdownPct':float(dd),'feesUsd':float(ledger.fees),'realizedPnlUsd':float(ledger.realized),
        'averageExposureUsd':float(exposure_sum/len(indices)),'turnoverUsd':sum(float(tr['amountUsd']) for tr in ledger.trades),
        'buyFills':sum(tr['action']=='BUY' for tr in ledger.trades),'sellFills':sum(tr['action']=='SELL' for tr in ledger.trades),
        'closedRoundTrips':len(pnl),'expectancyUsd':statistics.mean(pnl) if pnl else None,
        'winRatePct':100*sum(v>0 for v in pnl)/len(pnl) if pnl else None,
        'profitFactor':wins/losses if losses else None,'hours':len(indices),'blockedHours':blocked,
        'equityBlockedHours':equity_blocked,'peakBlockedHours':guard_blocked,
        'peakEntryFeeBlockedHours':peak_fee_blocked,'firstLossHalt':first_halt,
        'exitBacklogHours':backlog_hours,'maxExitBacklogCoins':max_backlog,'maxCompletedExitWaitHours':max(waits,default=0),
        'dailyReturns':returns,'dailyDates':[iso(day*24*HOUR)[:10] for day in daily], 'ruleReasons':dict(reasons)}
    return {'summary':summary,'trades':ledger.trades,'roundTrips':ledger.rounds,'equityCurve':curve}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=OUT/'replay-results.json')
    args=parser.parse_args()
    data=load_inputs()
    boundaries=['2024-09-01','2024-12-01','2025-03-01','2025-06-01','2025-09-01','2025-12-01','2026-03-01','2026-06-01','2026-09-01']
    windows=[('continuous','2024-09-01','2026-09-01')]+[(f'block{i+1}',a,b) for i,(a,b) in enumerate(zip(boundaries,boundaries[1:]))]+[('recent','2026-09-01','2026-09-20')]
    results={'generatedAt':datetime.now(timezone.utc).isoformat(),'protocolSha256':digest(Path(__file__).with_name('PROTOCOL.md')),
             'codeSha256':digest(Path(__file__)),'policySha256':digest(ROOT/'runner/policy.py'),
             'dataSha256':{p.name:digest(p) for p in (OUT/'candles').glob('*.json')},'runs':[]}
    for window,a,b in windows:
        for universe in UNIVERSES:
            for bps in (0,5,10):
                for variant in VARIANTS:
                    r=simulate(data,universe,variant,ms(a),ms(b),bps)
                    r['window']=window
                    # Keep full hourly paths for base-case continuous/recent only; all fills/dailies kept.
                    if not (bps==5 and window in ('continuous','recent')):
                        del r['equityCurve']
                    results['runs'].append(r)
            print(window,universe,'completed',flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print('Saved',args.output, 'runs',len(results['runs']),flush=True)

if __name__=='__main__':
    main()
