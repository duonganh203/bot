"""Predeclared V4 paper entry comparison with identical risk and exit rules.

Both entry hypotheses are unproven. Parameters are fixed before forward data is
observed; this module neither reads current/open candles nor tunes parameters.
The runner enforces one entry opportunity per closed 15-minute bar and persists
position metadata only after confirmed fills. Original V3 remains separate.
"""
from decimal import Decimal as D, InvalidOperation, ROUND_DOWN

SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT')
MICRO = D('0.000001')
BAR_MS = 900_000
POLICY = {
    'name': 'intraday-pullback-comparison-v4',
    'modes': ['slow', 'pullback'],
    'initialCapitalUsd': '50', 'maxOrderUsd': '5', 'maxExposureUsd': '20',
    'feeRate': '0.001', 'maxDailyLossUsd': '3', 'maxEquityLossUsd': '3',
    'paperMinimumOrderUsd': '1', 'targetDailyVolPct': '2',
    'entryBarMinutes': 15, 'slowEntryIntervalHours': 6,
    'reentryCooldownHours': 24, 'stopAtrMultiple': '2', 'maximumHoldHours': 12,
    'slowEntry': 'closed 1h price > SMA72 > SMA168, positive closed 24h and 7d returns; six-hour UTC schedule',
    'pullbackEntry': 'closed 4h price > SMA20 > SMA50; previous 15m low <= previous EMA20; latest closed 15m close > EMA20 and previous close',
    'sizing': 'both modes: min($5, $5 * 2% / daily realized volatility), capped by cash including fee and remaining $20 exposure; no adding; paper minimum $1',
    'ranking': 'both modes: descending 7d return / daily realized volatility; fixed universe order breaks ties; pullback does not require positive 24h or 7d return',
    'exits': 'both modes: minute risk checks, sticky liquidation, bid <= fixed entry fill minus 2 entry ATR14(15m), closed 4h price < SMA20, or holding >= 12h; absolute equity and UTC daily loss exits',
    'cooldown': 'both modes: 24 elapsed hours after confirmed completed exit; holdings worth less than $0.000001 are API dust',
    'evaluationClock': 'closed feature bar is matched to decision-book server time; elapsed holding and cooldown use actual evaluation time, constrained within 120 seconds of the book',
    'equityFloor': 'initial capital minus $3, marked at bid; entry fee and spread included; runner repeats check using final execution price',
    'execution': 'both modes share delayed executable-side paper quotes, fees and adverse slippage; runner sequentially submits reducing orders capped at $5',
    'comparison': 'slow preserves V3 entry hypothesis only; common V4 execution and exits differ from V3; original V3 is retained separately',
    'ai': 'offline research and review only; no additional LLM decision per candle',
    'status': 'prospective unproven hypotheses; no parameter tuning or automatic live promotion',
}


def number(value, nonnegative=False):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a policy number')
    try:
        result = D(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('Invalid policy number') from exc
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError('Nonfinite or negative policy number')
    return result


def hold(reason):
    return {'action': 'HOLD', 'symbol': None, 'amountUsd': None,
            'confidence': 1.0, 'riskLevel': 'LOW', 'rationale': reason}


def _position_state(state, quantities, now):
    if not isinstance(state, dict):
        raise ValueError('Policy state must be a dictionary')
    positions = {}
    for symbol, entry in state.get('positions', {}).items():
        if symbol not in SYMBOLS:
            raise ValueError('Saved position outside policy universe')
        timestamp = number(entry['entryTime'], True)
        price, stop = number(entry['entryPrice']), number(entry['stopPrice'])
        if timestamp > now or not D(0) < stop < price:
            raise ValueError('Invalid saved entry time or fixed stop')
        positions[symbol] = {'entryTime': timestamp, 'entryPrice': price, 'stopPrice': stop}
    for symbol, quantity in quantities.items():
        if quantity > 0 and symbol not in positions:
            raise ValueError('Held position requires confirmed entry metadata')
    exits = {}
    for symbol, timestamp in state.get('lastExitTime', {}).items():
        exited = number(timestamp, True)
        if symbol not in SYMBOLS or exited > now:
            raise ValueError('Invalid saved exit time')
        exits[symbol] = exited
    return positions, exits


def plan(context, features, decision_book, mode, state, closing=(), allow_entry=True, evaluation_time=None):
    """Return (candidate, sticky closing symbols, per-symbol gates, reason).

    Entry signals use only supplied validated closed-candle features. Bid quotes
    mark risk and trigger fixed stops, never replace closed entry features.
    Execution-time quotes are deliberately absent from this pure interface.
    """
    if mode not in POLICY['modes'] or not isinstance(allow_entry, bool):
        raise ValueError('Invalid policy mode or entry permission')
    server_ms, bar = decision_book['serverTimeMs'], features['bar']
    if (isinstance(server_ms, bool) or not isinstance(server_ms, int) or server_ms < 0
            or isinstance(bar, bool) or not isinstance(bar, int) or bar != server_ms // BAR_MS):
        raise ValueError('Feature bar must match decision server time')
    now = D(server_ms) / 1000
    if evaluation_time is not None:
        evaluated = number(evaluation_time, True)
        if abs(evaluated - now) > 120:
            raise ValueError('Evaluation time differs from decision book by more than 120 seconds')
        now = max(now, evaluated)
    if set(features['symbols']) != set(SYMBOLS) or set(decision_book['symbols']) != set(SYMBOLS):
        raise ValueError('V4 requires its fixed five-symbol universe')
    qty = {}
    for position in context['positions']:
        symbol = position['symbol']
        if symbol not in SYMBOLS or symbol in qty:
            raise ValueError('Duplicate position or asset outside policy universe')
        qty[symbol] = number(position['quantity'], True)
    bids, asks = {}, {}
    for symbol in SYMBOLS:
        quote = decision_book['symbols'][symbol]
        bids[symbol], asks[symbol] = number(quote['bid']), number(quote['ask'])
        if not D(0) < bids[symbol] <= asks[symbol]:
            raise ValueError('Invalid decision bid/ask quote')
    values = {s: qty.get(s, D(0)) * bids[s] for s in SYMBOLS}
    # Sub-microdollar dust cannot be sold through the backend notional API.
    tradable_qty = {s: qty[s] for s in qty if values[s] >= MICRO}
    positions, exited = _position_state(state, tradable_qty, now)
    if any(symbol not in SYMBOLS for symbol in closing):
        raise ValueError('Closing symbol outside policy universe')
    closing = {s for s in closing if values[s] >= MICRO}
    exposure = sum(values.values(), D(0))
    cash = number(context['portfolio']['cash'], True)
    initial = number(context['portfolio']['initialCapital'])
    if initial <= 0:
        raise ValueError('Initial capital must be positive')
    floor = initial - D(POLICY['maxEquityLossUsd'])
    equity, fee = cash + exposure, D(POLICY['feeRate'])
    daily_breach = context['risk']['dailyLossLimitReached']
    if not isinstance(daily_breach, bool):
        raise ValueError('Daily loss gate must be boolean')
    equity_breach = equity <= floor
    capacity = max(D(0), D(POLICY['maxExposureUsd']) - exposure)
    available = cash / (1 + fee)
    minimum, maximum = D(POLICY['paperMinimumOrderUsd']), D(POLICY['maxOrderUsd'])
    entry_slot = mode == 'pullback' or bar % (POLICY['slowEntryIntervalHours'] * 4) == 0
    gates = {}
    for symbol in SYMBOLS:
        feature = features['symbols'][symbol]
        positive_fields = ('closedPrice15m', 'previousClose15m', 'previousLow15m',
                           'previousEma20_15m', 'ema20_15m', 'atr14_15m',
                           'closedPrice4h', 'sma20_4h', 'sma50_4h', 'closedPrice', 'sma72', 'sma168')
        f = {key: number(feature[key]) for key in positive_fields}
        if any(value <= 0 for value in f.values()):
            raise ValueError('Closed price, trend and ATR features must be positive')
        day, week = number(feature['return24hPct']), number(feature['return7dPct'])
        volatility = number(feature['realizedVolDailyPct'], True)
        target = min(maximum, maximum * D(POLICY['targetDailyVolPct']) / volatility) if volatility > 0 else D(0)
        amount = min(target, capacity, available).quantize(MICRO, rounding=ROUND_DOWN)
        projected = equity - amount * (fee + 1 - bids[symbol] / asks[symbol])
        remaining = max(D(0), D(POLICY['reentryCooldownHours']) * 3600 - (now - exited[symbol])) if symbol in exited else D(0)
        if mode == 'slow':
            setup = {'closed1hAboveSma72': f['closedPrice'] > f['sma72'],
                     'sma72AboveSma168': f['sma72'] > f['sma168'],
                     'positive24hReturn': day > 0, 'positive7dReturn': week > 0}
        else:
            setup = {'closed4hAboveSma20': f['closedPrice4h'] > f['sma20_4h'],
                     'sma20AboveSma50_4h': f['sma20_4h'] > f['sma50_4h'],
                     'previous15mTouchedEma20': f['previousLow15m'] <= f['previousEma20_15m'],
                     'closed15mAboveEma20': f['closedPrice15m'] > f['ema20_15m'],
                     'closed15mAbovePreviousClose': f['closedPrice15m'] > f['previousClose15m']}
        setup['positiveVolatility'] = volatility > 0
        checks = {**setup, 'entrySchedule': entry_slot, 'entryAllowed': allow_entry,
                  'noExistingPosition': values[symbol] < MICRO, 'cooldownExpired': remaining == 0,
                  'dailyLossClear': not daily_breach, 'equityAboveFloor': not equity_breach,
                  'exposureAvailable': capacity >= minimum, 'cashAvailable': available >= minimum,
                  'paperMinimumMet': amount >= minimum, 'entryCostsWithinEquityFloor': projected > floor}
        entry = positions.get(symbol) if values[symbol] >= MICRO else None
        held_seconds = now - entry['entryTime'] if entry else None
        exit_checks = {'equityFloor': equity_breach, 'dailyLossLimit': daily_breach,
                       'closed4hBelowSma20': f['closedPrice4h'] < f['sma20_4h'],
                       'fixedStop': entry is not None and bids[symbol] <= entry['stopPrice'],
                       'maximumHold': entry is not None and held_seconds >= POLICY['maximumHoldHours'] * 3600,
                       'stickyClosing': symbol in closing}
        gates[symbol] = {
            'entry': all(checks.values()), 'signalEligible': all(setup.values()),
            'entryChecks': checks, 'failedGates': [name for name, passed in checks.items() if not passed],
            'exit': any(exit_checks.values()), 'exitChecks': exit_checks,
            'exitReasons': [name for name, passed in exit_checks.items() if passed],
            'holdingUsd': str(values[symbol]), 'entrySlot': entry_slot,
            'cooldownRemainingSeconds': str(remaining), 'lastExitTime': str(exited[symbol]) if symbol in exited else None,
            'heldSeconds': str(held_seconds) if held_seconds is not None else None,
            'fixedStopPrice': str(entry['stopPrice']) if entry else None,
            'rankScore': str(week / volatility) if volatility > 0 else None,
            'realizedVolDailyPct': str(volatility), 'targetOrderUsd': str(target),
            'orderUsd': str(amount), 'projectedEquityAtDecisionAsk': str(projected),
        }
        if values[symbol] >= MICRO and gates[symbol]['exit']:
            closing.add(symbol)
    for symbol in SYMBOLS:
        if symbol in closing:
            candidate = hold('Common V4 reducing exit: ' + ', '.join(gates[symbol]['exitReasons']) + '.')
            candidate.update(action='SELL', symbol=symbol,
                             amountUsd=str(min(maximum, values[symbol]).quantize(MICRO, rounding=ROUND_DOWN)))
            reason = 'RISK_EXIT' if daily_breach or equity_breach else 'POSITION_EXIT'
            return candidate, sorted(closing), gates, reason
    if daily_breach:
        reason = 'DAILY_LOSS_LIMIT'
    elif equity_breach:
        reason = 'EQUITY_LOSS_LIMIT'
    elif not allow_entry:
        reason = 'ENTRY_NOT_ALLOWED'
    elif not entry_slot:
        reason = 'ENTRY_SCHEDULE'
    elif capacity < minimum:
        reason = 'MAX_EXPOSURE'
    elif available < minimum:
        reason = 'INSUFFICIENT_CASH'
    else:
        reason = 'NO_ELIGIBLE_ENTRY'
        eligible = [s for s in SYMBOLS if gates[s]['signalEligible'] and values[s] < MICRO]
        ranked = sorted(eligible, key=lambda s: (-D(gates[s]['rankScore']), SYMBOLS.index(s)))
        for symbol in ranked:
            gate = gates[symbol]
            if not gate['entryChecks']['cooldownExpired']:
                reason = 'REENTRY_COOLDOWN'
            elif not gate['entryChecks']['paperMinimumMet']:
                reason = 'BELOW_PAPER_MINIMUM'
            elif not gate['entryChecks']['entryCostsWithinEquityFloor']:
                reason = 'EQUITY_LOSS_LIMIT'
            else:
                candidate = hold('Predeclared V4 ' + mode + ' entry; closed features and ranked 7d/volatility; shared sizing and exits.')
                candidate.update(action='BUY', symbol=symbol, amountUsd=gate['orderUsd'])
                return candidate, [], gates, 'SLOW_TREND_ENTRY' if mode == 'slow' else 'PULLBACK_ENTRY'
    return hold(reason), sorted(closing), gates, reason
