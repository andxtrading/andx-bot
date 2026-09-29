"""Risk management: volatility-targeted position sizing, ATR stops,
trailing stops, and account-level kill switches.

Sizing follows the volatility-adjusted approach the research favors: risk a
fixed fraction of equity per trade, with the stop distance set by ATR, so
position size automatically shrinks when the market gets wild.
"""

from dataclasses import dataclass

# Ceilings above which a config value is treated as a mistake and the field
# default is used instead. A daily-loss limit above 100% is mathematically
# unreachable (equity cannot go below zero), i.e. it silently disables the
# kill switch — the exact bug that shipped as daily_loss_limit_pct=900.
SANITY_CAPS = {
    "risk_per_trade_pct": 5.0,
    "atr_stop_mult": 10.0,
    "atr_takeprofit_mult": 20.0,
    "max_position_pct": 50.0,
    "max_leverage": 10.0,
    "max_open_positions": 20,
    "daily_loss_limit_pct": 25.0,
}


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0    # % of equity risked if the stop is hit
    atr_stop_mult: float = 2.5         # stop distance in ATRs
    atr_takeprofit_mult: float = 0.0   # 0 = no fixed take-profit (let stops/signals exit)
    max_position_pct: float = 25.0     # cap: notional per position as % of equity
    max_leverage: float = 2.0          # cap: total notional / equity
    max_open_positions: int = 4
    daily_loss_limit_pct: float = 5.0  # kill switch: stop trading past this daily loss

    @classmethod
    def from_dict(cls, d: dict) -> "RiskConfig":
        kwargs = {}
        for field in cls.__dataclass_fields__:
            if field in d and d[field] is not None and d[field] != "":
                value = float(d[field]) if field != "max_open_positions" else int(d[field])
                # A zero/negative limit would block all trading (or disable a
                # safety cap) — fall back to the default instead. Exception:
                # atr_takeprofit_mult, where 0 legitimately means "off".
                if value <= 0 and field != "atr_takeprofit_mult":
                    continue
                # Values beyond any plausible intent (daily_loss_limit_pct=900)
                # would silently disable a safety net — fall back to the default.
                if field in SANITY_CAPS and value > SANITY_CAPS[field]:
                    continue
                kwargs[field] = value
        return cls(**kwargs)


def position_size(equity: float, price: float, atr_value: float,
                  cfg: RiskConfig, open_notional: float,
                  risk_mult: float = 1.0) -> tuple[float, str]:
    """Return (qty, reason). qty == 0 means the trade is blocked.

    risk_mult (0.1–1.0) lets governors shrink size — never enlarge it —
    e.g. after a loss streak or on a symbol with poor recorded expectancy."""
    if equity <= 0 or price <= 0:
        return 0.0, "no equity"
    if atr_value <= 0:
        return 0.0, "no volatility reading"

    stop_distance = cfg.atr_stop_mult * atr_value
    risk_amount = equity * cfg.risk_per_trade_pct / 100.0
    risk_amount *= max(0.1, min(1.0, risk_mult))
    qty = risk_amount / stop_distance

    # Cap 1: per-position notional
    max_notional = equity * cfg.max_position_pct / 100.0
    if qty * price > max_notional:
        qty = max_notional / price

    # Cap 2: account leverage
    headroom = equity * cfg.max_leverage - open_notional
    if headroom <= 0:
        return 0.0, "leverage cap reached"
    if qty * price > headroom:
        qty = headroom / price

    if qty * price < 10:  # exchange min-notional territory
        return 0.0, "size below exchange minimum"
    return qty, "ok"


def stop_price(side: int, entry: float, atr_value: float, cfg: RiskConfig) -> float:
    return entry - side * cfg.atr_stop_mult * atr_value


def take_profit_price(side: int, entry: float, atr_value: float, cfg: RiskConfig) -> float | None:
    if cfg.atr_takeprofit_mult <= 0:
        return None
    return entry + side * cfg.atr_takeprofit_mult * atr_value


def manage_stop(side: int, entry: float, stop: float, r_value: float,
                high_water: float | None, close: float, atr_value: float,
                cfg: RiskConfig, fee_roundtrip: float = 0.0,
                trailing: bool = True) -> tuple[float, float]:
    """Smart exit engine. Returns (new_stop, new_high_water); the stop only
    ever ratchets in the trade's favor.

    Layers (all measured in R = the initial stop distance):
    - trailing strategies: classic ATR trail from the last close;
    - after +1R, EVERY position moves its stop to break-even *plus fees*
      (on a fee-heavy venue an exit at the raw entry still loses the
      round-trip fees);
    - after +1.5R, trailing strategies ratchet a chandelier stop from the
      high-water mark instead of the last close, capping give-back after
      a strong run.
    """
    if side == 1:
        hw = close if high_water is None else max(high_water, close)
    else:
        hw = close if high_water is None else min(high_water, close)
    candidates = []
    if stop:
        candidates.append(stop)
    if trailing:
        candidates.append(close - side * cfg.atr_stop_mult * atr_value)
    profit_r = ((close - entry) * side / r_value) if r_value and r_value > 0 else 0.0
    if profit_r >= 1.0:
        candidates.append(entry * (1 + side * fee_roundtrip))
    if profit_r >= 1.5 and trailing:
        candidates.append(hw - side * cfg.atr_stop_mult * atr_value)
    if not candidates:
        return stop, hw
    new_stop = max(candidates) if side == 1 else min(candidates)
    return new_stop, hw


def trail_stop(side: int, current_stop: float, close: float, atr_value: float,
               cfg: RiskConfig) -> float:
    """Ratchet the stop in the trade's favor, never against it."""
    candidate = close - side * cfg.atr_stop_mult * atr_value
    if side == 1:
        return max(current_stop, candidate)
    return min(current_stop, candidate)


def stop_hit(side: int, price: float, stop: float) -> bool:
    return price <= stop if side == 1 else price >= stop


def daily_loss_breached(equity: float, day_start_equity: float | None, cfg: RiskConfig) -> bool:
    if not day_start_equity or day_start_equity <= 0:
        return False
    loss_pct = (day_start_equity - equity) / day_start_equity * 100.0
    return loss_pct >= cfg.daily_loss_limit_pct
