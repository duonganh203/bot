"""Reconcile final research outputs and freeze an auditable verification record."""
from pathlib import Path
import hashlib
import json
import math
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/quant/research-20260920'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p): return json.loads(p.read_text(encoding='utf-8'))

def main():
    final=read(OUT/'replay-results.json')
    initial=read(OUT/'replay-before-diagnostic-fixes.json')
    assert initial['protocolSha256']==sha(ROOT/'quant/research_20260920/PROTOCOL.initial.md')
    assert final['protocolSha256']==sha(ROOT/'quant/research_20260920/PROTOCOL.md')
    assert final['codeSha256']==sha(ROOT/'quant/research_20260920/replay.py')
    assert final['policySha256']==sha(ROOT/'runner/policy.py')
    assert len(final['runs'])==len(initial['runs'])==540
    maxerror=0
    for a,b in zip(initial['runs'],final['runs']):
        assert a['trades']==b['trades']
        assert a['roundTrips']==b['roundTrips']
        s=b['summary']
        assert a['summary']['returnPct']==s['returnPct']
        pairs=[(sum(float(t['feeUsd']) for t in b['trades']),s['feesUsd']),
               (sum(float(t['realizedPnl']) for t in b['trades']),s['realizedPnlUsd']),
               (sum(float(t['amountUsd']) for t in b['trades']),s['turnoverUsd']),
               (50*math.prod(1+r for r in s['dailyReturns']),s['endEquity']),
               ((s['endEquity']/50-1)*100,s['returnPct'])]
        for actual,expected in pairs:
            error=abs(actual-expected); maxerror=max(maxerror,error)
            assert error<1e-8,(s,error)
        assert len(b['roundTrips'])==s['closedRoundTrips']
    for name,expected in final['dataSha256'].items():
        assert sha(OUT/'candles'/name)==expected
    record={'checkedAt':datetime.now(timezone.utc).isoformat(),'status':'passed',
        'newResearchBehavioralTests':18,'legacyBacktestTests':20,
        'runsReconciled':540,'allTradesRoundsAndReturnsUnchangedAfterDiagnosticFixes':True,
        'maxFloatingSummaryError':maxerror,
        'changes':['Timing wording clarification only in PROTOCOL.md; original preserved.',
                   'Queue timestamps no longer leak from cleared dust into later entries.',
                   'Peak entry fee restrictions now included in blocked-hours diagnostics.',
                   'Reject non-finite OHLCV and metadata mismatches.'],
        'hashes':{str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in [ROOT/'runner/policy.py',
            ROOT/'quant/research_20260920/PROTOCOL.initial.md',ROOT/'quant/research_20260920/PROTOCOL.md',
            ROOT/'quant/research_20260920/replay.py',OUT/'replay-before-diagnostic-fixes.json',
            OUT/'replay-results.json',OUT/'existing-statistics.json',OUT/'data-manifest.json']}}
    (OUT/'verification.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in record.items() if k!='hashes'},indent=2))
if __name__=='__main__': main()
