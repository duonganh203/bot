"""Frozen prospective V3 paper hypothesis; parameters have not been optimized.

Slow, closed-candle trend signals, volatility scaling and a re-entry delay are
research hypotheses, not evidence of profitable alpha. This module has no I/O.
"""
from datetime import datetime
from decimal import Decimal as D, ROUND_DOWN

SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT')
MICRO = D('0.000001')
HOUR_MS = 3_600_000
POLICY = {
    'name': 'slow-ranked-volatility-v3', 'initialCapitalUsd': '50',
    'orderUsd': '5', 'maxExposureUsd': '20', 'feeRate': '0.001',
    'maxDailyLossUsd': '3', 'maxEquityLossUsd': '3', 'paperMinimumOrderUsd': '1',
    'fastSmaHours': 72, 'slowSmaHours': 168, 'momentumHours': 168,
    'volatilityReturnHours': 336, 'targetDailyVolPct': '2',
    'entryIntervalHours': 6, 'reentryCooldownHours': 24,
    'entry': 'closedPrice > SMA72 > SMA168; 24h and 7d closed returns > 0; entry every six UTC hours',
    'exit': 'closedPrice < SMA72 or 7d return <= 0; daily/equity risk gate; finish partial exits hourly',
    'sizing': 'min($5, $5 * 2% / daily realized volatility), cash/fee/exposure capped; paper minimum $1; no adding',
    'priority': 'hourly sticky exits first; entries ranked by 7d return / daily realized volatility; fixed symbol-order ties',
    'volatility': 'sample standard deviation of 336 hourly log returns multiplied by sqrt(24), in percent',
    'cooldown': '24 UTC hourly slots after completed exit; API dust below $0.000001 is not tradable',
    'equityFloor': 'initial capital minus $3, including entry fee; not a peak drawdown rule',
    'execution': 'one action/hour; quote fills with 0.1% fee; spread telemetry does not alter paper fills',
    'ai': 'no discretionary LLM veto in this candidate; AI assists offline research and audit',
    'status': 'prospective unproven hypothesis, no parameter search or automatic live promotion',
}


def hold(reason):
    return {'action': 'HOLD', 'symbol': None, 'amountUsd': None,
            'confidence': 1.0, 'riskLevel': 'LOW', 'rationale': reason}


def entry_window(slot):
    return slot % POLICY['entryIntervalHours'] == 0


def number(value, nonnegative=False):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a policy number')
    result = D(str(value))
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError('Nonfinite or negative policy number')
    return result


def last_exit_slots(context, values, state, slot):
    """A SELL is a completed exit only when the current holding is API dust.

The runner persists lastExitSlot so a bounded recentTrades response cannot erase
cooldown history. Inference from fresh context also recovers a committed fill
whose request acknowledgement was lost. This function never mutates its inputs.
"""
    result = {}
    for symbol, exited in (state or {}).get('lastExitSlot', {}).items():
        if symbol not in SYMBOLS or isinstance(exited, bool) or not isinstance(exited, int) or exited > slot:
            raise ValueError('Invalid saved exit slot')
        result[symbol] = exited
    for trade in context.get('recentTrades', []):
        symbol = trade.get('symbol')
        if trade.get('side') != 'SELL' or symbol not in SYMBOLS or values[symbol] >= MICRO:
            continue
        timestamp = datetime.fromisoformat(trade['createdAt'].replace('Z', '+00:00'))
        if timestamp.utcoffset() is None:
            raise ValueError('Trade timestamp requires a timezone')
        exited = int(timestamp.timestamp()) // 3600
        if exited > slot:
            raise ValueError('Trade exit is in the future')
        result[symbol] = max(exited, result.get(symbol, exited))
    return result


def plan(context, market, closing=(), state=None):
    """Return candidate, sticky closing list, observable gates and reason code."""
    server_ms = market['serverTimeMs']
    if isinstance(server_ms, bool) or not isinstance(server_ms, int) or server_ms < 0:
        raise ValueError('Invalid market server time')
    slot = server_ms // HOUR_MS
    quotes = market['symbols']
    if set(quotes) != set(SYMBOLS):
        raise ValueError('V3 requires its fixed five-symbol universe')
    qty = {}
    for position in context['positions']:
        symbol = position['symbol']
        if symbol not in SYMBOLS or symbol in qty:
            raise ValueError('Duplicate position or asset outside policy universe')
        qty[symbol] = number(position['quantity'], True)
    marks = {s: number(quotes[s]['price']) for s in SYMBOLS}
    if any(price <= 0 for price in marks.values()):
        raise ValueError('Nonpositive quote')
    values = {s: qty.get(s, D(0)) * marks[s] for s in SYMBOLS}
    exposure = sum(values.values(), D(0))
    cash = number(context['portfolio']['cash'], True)
    floor = number(context['portfolio']['initialCapital']) - D(POLICY['maxEquityLossUsd'])
    equity = cash + exposure
    equity_breach = equity <= floor
    daily_breach = context['risk']['dailyLossLimitReached']
    if not isinstance(daily_breach, bool):
        raise ValueError('Daily loss gate must be boolean')
    exits = last_exit_slots(context, values, state, slot)
    closing = {s for s in closing if s in SYMBOLS and values[s] >= MICRO}
    entry_slot = entry_window(slot)
    gates = {}
    for symbol in SYMBOLS:
        q = quotes[symbol]
        close, fast, slow = (number(q[key]) for key in ('closedPrice', 'sma72', 'sma168'))
        day, week, volatility = (number(q[key]) for key in ('return24hPct', 'return7dPct', 'realizedVolDailyPct'))
        if min(close, fast, slow) <= 0 or volatility < 0:
            raise ValueError('Invalid trend/volatility feature')
        eligible = close > fast > slow and day > 0 and week > 0 and volatility > 0
        remaining = max(0, POLICY['reentryCooldownHours'] - (slot - exits[symbol])) if symbol in exits else 0
        gates[symbol] = {
            'entry': eligible, 'exit': close < fast or week <= 0,
            'holdingUsd': str(values[symbol]), 'entrySlot': entry_slot,
            'cooldownRemainingHours': remaining, 'lastExitSlot': exits.get(symbol),
            'rankScore': str(week / volatility) if volatility > 0 else None,
            'realizedVolDailyPct': str(volatility),
            'targetOrderUsd': str(min(D(POLICY['orderUsd']), D(POLICY['orderUsd']) *
                                     D(POLICY['targetDailyVolPct']) / volatility)) if volatility > 0 else '0',
        }
        if values[symbol] >= MICRO and (gates[symbol]['exit'] or equity_breach or daily_breach):
            closing.add(symbol)
    for symbol in SYMBOLS:
        if symbol in closing:
            candidate = hold('Hourly risk/trend exit; finish liquidation despite rebounds and entry cooldown.')
            candidate.update(action='SELL', symbol=symbol,
                             amountUsd=str(min(D(POLICY['orderUsd']), values[symbol]).quantize(MICRO, rounding=ROUND_DOWN)))
            return candidate, sorted(closing), gates, 'RISK_EXIT' if daily_breach or equity_breach else 'TREND_EXIT'
    reason = 'NO_ELIGIBLE_ENTRY'
    if daily_breach:
        reason = 'DAILY_LOSS_LIMIT'
    elif equity <= floor:
        reason = 'EQUITY_LOSS_LIMIT'
    elif not entry_slot:
        reason = 'ENTRY_SCHEDULE'
    else:
        fee = D(POLICY['feeRate'])
        minimum = D(POLICY['paperMinimumOrderUsd'])
        capacity = D(POLICY['maxExposureUsd']) - exposure
        available = cash / (1 + fee)
        if capacity < minimum:
            reason = 'MAX_EXPOSURE'
        elif available < minimum:
            reason = 'INSUFFICIENT_CASH'
        elif equity - minimum * fee <= floor:
            reason = 'EQUITY_LOSS_LIMIT'
        else:
            eligible = [s for s in SYMBOLS if gates[s]['entry'] and values[s] < MICRO]
            ranked = sorted(eligible, key=lambda s: (-D(gates[s]['rankScore']), SYMBOLS.index(s)))
            for symbol in ranked:
                if gates[symbol]['cooldownRemainingHours']:
                    reason = 'REENTRY_COOLDOWN'
                    continue
                amount = min(D(gates[symbol]['targetOrderUsd']), capacity, available).quantize(MICRO, rounding=ROUND_DOWN)
                if amount < minimum:
                    reason = 'BELOW_PAPER_MINIMUM'
                    continue
                if equity - amount * fee <= floor:
                    reason = 'EQUITY_LOSS_LIMIT'
                    continue
                candidate = hold('Prospective slow trend: ranked 7d/volatility, scaled entry, no adding.')
                candidate.update(action='BUY', symbol=symbol, amountUsd=str(amount))
                return candidate, [], gates, 'SLOW_TREND_ENTRY'
    return hold(reason), sorted(closing), gates, reason
