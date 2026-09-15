"""Pure hourly portfolio policy shared by control, AI-filtered paper and replay."""
from decimal import Decimal as D, ROUND_DOWN

SYMBOLS = ('BTCUSDT', 'ETHUSDT')
MICRO = D('0.000001')
POLICY = {
    'initialCapitalUsd': '50', 'orderUsd': '5', 'maxExposureUsd': '20',
    'feeRate': '0.001', 'maxDailyLossUsd': '3', 'maxEquityLossUsd': '3',
    'entry': 'quote > SMA20 > SMA50 and closed-candle return24h > 0',
    'exit': 'quote < SMA20 and return24h < 0; or daily/equity loss breach; finish partial exits',
    'sizing': 'one $5 entry per coin, no adding; exits in <= $5 steps; one action/hour',
    'priority': 'exits first; BTCUSDT then ETHUSDT',
    'ai': 'may veto the single eligible entry only; cannot resize or delay exits',
    'equityFloor': 'initial capital minus $3, not peak drawdown; BUY includes entry fee',
    'execution': 'same cached quote, 0.1% fee; no spread/slippage or latency price adjustment',
}


def hold(reason):
    return {'action': 'HOLD', 'symbol': None, 'amountUsd': None,
            'confidence': 1.0, 'riskLevel': 'LOW', 'rationale': reason}


def plan(context, market, closing=()):
    """No I/O or AI. A sticky liquidation survives bounces and process restarts."""
    qty = {p['symbol']: D(str(p['quantity'])) for p in context['positions']}
    quotes = market['symbols']
    values = {s: qty.get(s, D(0)) * D(quotes[s]['price']) for s in SYMBOLS}
    exposure = sum(values.values())
    cash = D(str(context['portfolio']['cash']))
    floor = D(str(context['portfolio']['initialCapital'])) - D(POLICY['maxEquityLossUsd'])
    equity_breach = cash + exposure <= floor
    daily_breach = context['risk']['dailyLossLimitReached']
    closing = {s for s in closing if values[s] >= MICRO}
    gates = {}
    for symbol in SYMBOLS:
        q = quotes[symbol]
        gates[symbol] = {
            'entry': D(q['price']) > D(q['sma20']) > D(q['sma50']) and D(q['return24hPct']) > 0,
            'exit': D(q['price']) < D(q['sma20']) and D(q['return24hPct']) < 0,
            'holdingUsd': str(values[symbol]),
        }
        if values[symbol] >= MICRO and (gates[symbol]['exit'] or equity_breach or daily_breach):
            closing.add(symbol)
    for symbol in SYMBOLS:
        if symbol in closing:
            candidate = hold('Reduce position: loss gate or trend exit; complete liquidation in steps.')
            candidate.update(action='SELL', symbol=symbol,
                             amountUsd=str(min(D('5'), values[symbol]).quantize(MICRO, rounding=ROUND_DOWN)))
            reason = 'RISK_EXIT' if daily_breach or equity_breach else 'TREND_EXIT'
            return candidate, sorted(closing), gates, reason
    reason = 'NO_ELIGIBLE_ENTRY'
    if daily_breach:
        reason = 'DAILY_LOSS_LIMIT'
    elif cash + exposure - D('0.005') <= floor:
        reason = 'EQUITY_LOSS_LIMIT'
    elif cash < D('5.005'):
        reason = 'INSUFFICIENT_CASH'
    elif exposure + 5 > 20:
        reason = 'MAX_EXPOSURE'
    else:
        for symbol in SYMBOLS:
            if gates[symbol]['entry'] and values[symbol] < MICRO:
                candidate = hold('Trend entry: one $5 position; no adding.')
                candidate.update(action='BUY', symbol=symbol, amountUsd='5')
                return candidate, [], gates, 'TREND_ENTRY'
    return hold(reason), sorted(closing), gates, reason
