#!/usr/bin/env python3
"""Read-only SQLite ledger reconciliation, including all fills, not an API page."""
import argparse
from decimal import Decimal as D, localcontext
from pathlib import Path
import sqlite3
import run as core


def reconcile(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db, localcontext() as ctx:
        ctx.prec = 80
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        core.require(db.execute('pragma integrity_check').fetchone()[0] == 'ok', 'SQLite integrity failed')
        core.require(not db.execute('pragma foreign_key_check').fetchall(), 'Foreign-key check failed')
        p = db.execute('select * from portfolio').fetchone()
        trades = db.execute('select * from trades order by sequence').fetchall()
        cash, fees, pnl = D(p['initial_capital']), D(0), D(0)
        qty = {s: D(0) for s in core.SYMBOLS}
        open_round = {s: False for s in core.SYMBOLS}
        rounds = 0
        for t in trades:
            buy = t['side'] == 'BUY'
            cash += -D(t['net_usd']) if buy else D(t['net_usd'])
            fees += D(t['fee_usd'])
            pnl += D(t['realized_pnl'])
            qty[t['symbol']] += D(t['quantity']) * (1 if buy else -1)
            if buy:
                open_round[t['symbol']] = True
            elif open_round[t['symbol']] and qty[t['symbol']] * D(t['price']) < D('0.000001'):
                rounds += 1
                open_round[t['symbol']] = False
        core.require(cash == D(p['cash']) and fees == D(p['total_fees']), 'Cash/fee ledger mismatch')
        core.require(abs(pnl - D(p['realized_pnl'])) < D('1e-40'), 'Realized PnL mismatch')
        positions = {r['symbol']: D(r['quantity']) for r in db.execute('select * from positions')}
        core.require(all(qty[s] == positions.get(s, D(0)) for s in core.SYMBOLS), 'Inventory ledger mismatch')
        executed = db.execute("select count(*) from agent_decisions where status='EXECUTED'").fetchone()[0]
        core.require(executed == len(trades) == p['version'], 'Trade/decision/version mismatch')
        return {'status': 'passed', 'database': str(path), 'trades': len(trades), 'completedRoundTrips': rounds,
                'cash': str(cash), 'fees': str(fees), 'realizedPnl': str(pnl), 'quantity': {s: str(q) for s, q in qty.items()}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    print(core.dumps(reconcile(parser.parse_args().database)))
