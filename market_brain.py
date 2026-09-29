"""market_brain.py — the assistant's chart-reading eyes and risk X-ray.

Pure analysis, zero side effects: no orders, no state changes, no config
writes. Every function returns a plain dict the brain narrates from, with
honest caveats baked in (ANDX bars carry no volume; stress tests are
floor-checks that ignore slippage and say so).
"""

import pandas as pd

from strategies import ema, rsi, atr, adx, bollinger, htf_bias

_BARS_PER_DAY = {"15m": 96, "1h": 24, "4h": 6, "1d": 1}


def read_chart(market, symbol: str, timeframe: str = "1h") -> dict:
    """A full technical read of one symbol: trend posture, momentum,
    volatility, squeeze, and the levels that matter."""
    if timeframe not in _BARS_PER_DAY:
        return {"error": f"timeframe must be one of {', '.join(_BARS_PER_DAY)}"}
    try:
        raw = market.ohlcv(symbol, timeframe, limit=300)
    except Exception as e:
        return {"error": f"couldn't fetch candles for {symbol}: {str(e)[:100]}"}
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    if len(df) < 60:
        return {"error": f"only {len(df)} candles for {symbol} on {timeframe} — "
                         "not enough history to read honestly"}
    closed = df.iloc[:-1]  # the forming candle lies
    c = closed["close"]
    px = float(c.iloc[-1])
    if px <= 0:
        return {"error": f"bad price data for {symbol}"}

    e20s, e50s = ema(c, 20), ema(c, 50)
    e20, e50 = float(e20s.iloc[-1]), float(e50s.iloc[-1])
    e200 = float(ema(c, 200).iloc[-1]) if len(c) >= 200 else None
    r = float(rsi(c, 14).iloc[-1])
    a = float(atr(closed, 14).iloc[-1])
    ax = float(adx(closed, 14).iloc[-1])
    mid, up, lo = bollinger(c)
    bias, bias_adx = htf_bias(closed)

    above20, above50 = px > e20, px > e50
    slope50 = (e50 - float(e50s.iloc[-10])) / px * 100
    posture = ("uptrend" if above20 and above50 and slope50 > 0 else
               "downtrend" if not above20 and not above50 and slope50 < 0 else
               "chop / transition")

    band_hi, band_lo = float(up.iloc[-1]), float(lo.iloc[-1])
    bw = (band_hi - band_lo) / px * 100
    bw_series = ((up - lo) / c * 100).dropna()
    squeeze = bool(len(bw_series) >= 60
                   and bw <= float(bw_series.tail(120).quantile(0.2)))
    bpos = (px - band_lo) / max(1e-12, band_hi - band_lo)

    look = closed.tail(90)
    swing_hi, swing_lo = float(look["high"].max()), float(look["low"].min())
    # nearest structure: 5-bar pivot highs/lows walking back from the present
    h, l = closed["high"].values, closed["low"].values
    piv_hi, piv_lo = [], []
    for i in range(len(closed) - 3, max(10, len(closed) - 90), -1):
        if h[i] == max(h[i - 2:i + 3]):
            piv_hi.append(float(h[i]))
        if l[i] == min(l[i - 2:i + 3]):
            piv_lo.append(float(l[i]))
        if len(piv_hi) >= 4 and len(piv_lo) >= 4:
            break
    resistance = sorted({round(v, 8) for v in piv_hi if v > px})[:2]
    support = sorted({round(v, 8) for v in piv_lo if v < px}, reverse=True)[:2]

    n24 = _BARS_PER_DAY[timeframe]
    chg24 = ((px / float(c.iloc[-n24 - 1]) - 1) * 100
             if len(c) > n24 + 1 else None)

    return {
        "symbol": symbol, "timeframe": timeframe, "price": px,
        "change_pct_approx_24h": round(chg24, 2) if chg24 is not None else None,
        "trend": {
            "posture": posture,
            "above_ema20": above20, "above_ema50": above50,
            "above_ema200": (px > e200) if e200 is not None else None,
            "ema50_slope_pct_per_10_bars": round(slope50, 3),
            "adx": round(ax, 1),
            "strength": "strong trend" if ax >= 25 else "weak / choppy",
        },
        "higher_timeframe_bias": {1: "bullish", -1: "bearish", 0: "neutral"}[bias],
        "momentum": {
            "rsi14": round(r, 1),
            "state": ("overbought" if r >= 70 else
                      "oversold" if r <= 30 else "neutral"),
        },
        "volatility": {
            "atr": round(a, 8), "atr_pct": round(a / px * 100, 3),
            "bollinger_width_pct": round(bw, 2),
            "squeeze_coiling": squeeze,
            "position_in_bands_0to1": round(max(0.0, min(1.0, bpos)), 2),
        },
        "levels": {
            "swing_high_90_bars": swing_hi, "swing_low_90_bars": swing_lo,
            "nearest_resistance": resistance or None,
            "nearest_support": support or None,
        },
        "note": ("ANDX bars carry no volume, so this read is price-only. "
                 "The forming candle is excluded."),
    }


def portfolio_risk(engine, risk_cfg: dict) -> dict:
    """The risk X-ray: what's actually at stake across every open position,
    how close the daily breaker is, and whether stops lock profit."""
    if not engine.broker:
        return {"error": "the engine isn't running"}
    prices = dict(engine.prices)
    equity = engine.broker.equity(prices) + engine._guard_value()
    rows, total_risk, total_notional = [], 0.0, 0.0
    longs = shorts = stopless = 0
    for s, p in list(engine.broker.positions.items()):
        px = prices.get(s, p.entry)
        notional = p.qty * px
        total_notional += notional
        risk = abs(px - p.stop) * p.qty if p.stop else None
        if risk is not None:
            total_risk += risk
        else:
            stopless += 1
        locked = ((p.stop - p.entry) * p.qty * p.side) if p.stop else None
        longs += (p.side == 1)
        shorts += (p.side == -1)
        rows.append({
            "symbol": s, "side": "long" if p.side == 1 else "short",
            "notional": round(notional, 2),
            "unrealized": round(p.unrealized(px), 2),
            "risk_to_stop": round(risk, 2) if risk is not None else "NO STOP",
            "stop_locks_profit": bool(locked is not None and locked > 0),
            "strategy": p.strategy,
        })
    limit_pct = float(risk_cfg.get("daily_loss_limit_pct", 5.0))
    day_start = engine.day_start_equity or equity
    breaker_level = day_start * (1 - limit_pct / 100)
    room = equity - breaker_level
    return {
        "equity": round(equity, 2),
        "open_positions": rows,
        "long_short_mix": {"longs": longs, "shorts": shorts,
                           "one_sided": bool(rows) and (longs == 0 or shorts == 0)},
        "total_notional": round(total_notional, 2),
        "exposure_pct_of_equity": round(total_notional / equity * 100, 1) if equity else 0,
        "total_risk_if_every_stop_hits": round(total_risk, 2),
        "positions_without_stops": stopless,
        "daily_breaker": {
            "limit_pct": limit_pct,
            "trips_below_equity": round(breaker_level, 2),
            "room_left": round(room, 2),
            "would_all_stops_hitting_trip_it": bool(total_risk > room),
            "tripped_now": engine.kill_switch_tripped,
        },
        "note": "risk_to_stop assumes clean fills at the stop; thin books can slip worse",
    }


def stress_test(engine, shock_pct: float) -> dict:
    """Shock every price by shock_pct at once (e.g. -20) and report what
    the book does: which stops fire, P&L per position, equity after, and
    whether the daily breaker trips. A floor-check — slippage ignored."""
    if not engine.broker:
        return {"error": "the engine isn't running"}
    try:
        shock = float(shock_pct) / 100.0
    except (TypeError, ValueError):
        return {"error": "shock_pct must be a number like -20"}
    if not -0.95 <= shock <= 0.95:
        return {"error": "keep the shock between -95 and +95 percent"}
    prices = dict(engine.prices)
    rows, pnl_sum = [], 0.0
    for s, p in list(engine.broker.positions.items()):
        px = prices.get(s, p.entry)
        shocked = px * (1 + shock)
        stop_fires = bool(p.stop) and (
            (p.side == 1 and shocked <= p.stop) or
            (p.side == -1 and shocked >= p.stop))
        exit_px = p.stop if stop_fires else shocked
        pnl = (exit_px - p.entry) * p.qty * p.side
        pnl_sum += pnl
        rows.append({
            "symbol": s, "side": "long" if p.side == 1 else "short",
            "stop_fires": stop_fires,
            "pnl_at_that_point": round(pnl, 2),
        })
    equity_now = engine.broker.equity(prices) + engine._guard_value()
    equity_after = engine.broker.balance + pnl_sum + engine._guard_value() * (1 + shock)
    day_start = engine.day_start_equity or equity_now
    return {
        "shock_pct": shock_pct,
        "positions": rows or "flat — a crash would cost you nothing",
        "equity_now": round(equity_now, 2),
        "equity_after_shock": round(equity_after, 2),
        "drawdown_from_here": round(equity_after - equity_now, 2),
        "day_start_equity": round(day_start, 2),
        "note": ("stops that fire are assumed to fill AT the stop — real "
                 "books can slip worse in a crash; shorts profit from drops"),
    }
