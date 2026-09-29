"""Trading strategies.

Strategy selection is grounded in published crypto backtests:
- Trend-following / time-series momentum is the best-documented edge in crypto
  (works "strikingly well" on BTC; volatility-adjusted trend following dominates
  equal-weighting in academic tests).
- Mean-reversion earns comparable returns with lower risk in RANGING markets
  (backtests show ~74% win rates), but loses badly in strong trends.
- Channel breakout (turtle-style) is a robust momentum variant.
- Because the regime decides the winner, the default "auto" strategy measures
  trend strength (ADX) and routes to trend-following in trending markets and
  mean-reversion in ranging ones.

Each strategy returns a desired position: +1 (long), -1 (short), 0 (flat).
Signals are computed on CLOSED candles only (the engine drops the forming one).
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- indicators

def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = atr(df, length)
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / tr
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / length, adjust=False).mean().fillna(0)


def bollinger(series: pd.Series, length: int = 20, mult: float = 2.0):
    mid = series.rolling(length).mean()
    std = series.rolling(length).std()
    return mid, mid + mult * std, mid - mult * std


def htf_bias(df: pd.DataFrame) -> tuple[int, float]:
    """Higher-timeframe trend bias for multi-timeframe confirmation.

    Returns (bias, adx): bias is +1 when the 21-EMA is above the 55-EMA
    (uptrend), -1 below (downtrend), 0 when indistinguishable. Entries on
    the trading timeframe that fight this bias are the classic whipsaw
    donor trade."""
    close = df["close"]
    f = ema(close, 21).iloc[-1]
    s = ema(close, 55).iloc[-1]
    strength = float(adx(df, 14).iloc[-1])
    if f > s:
        return 1, strength
    if f < s:
        return -1, strength
    return 0, strength


# ---------------------------------------------------------------- strategies

class Strategy:
    name = "base"
    label = "Base"
    # Trend strategies trail their stop; mean-reversion uses a fixed stop.
    trailing = False

    def __init__(self, params: dict | None = None):
        self.params = params or {}

    def p(self, key, default):
        return self.params.get(key, default)

    def min_bars(self) -> int:
        """Closed candles required before signals are trusted. The engine
        refuses to trade a symbol with less history — an unseeded 55-EMA or
        ADX on 60 bars produces confident-looking nonsense."""
        return 180

    def signal(self, df: pd.DataFrame, current: int) -> int:
        """df: closed OHLCV candles. current: current position (+1/-1/0).
        Returns desired position."""
        raise NotImplementedError


class EMATrend(Strategy):
    """EMA crossover trend-following with ADX confirmation.

    Long when fast EMA > slow EMA, short when below — but only while ADX
    confirms a real trend; otherwise go flat rather than chop."""

    name = "ema_trend"
    label = "EMA Trend-Following"
    trailing = True

    def signal(self, df, current):
        fast = self.p("fast", 21)
        slow = self.p("slow", 55)
        adx_min = self.p("adx_min", 20)
        f = ema(df["close"], fast)
        s = ema(df["close"], slow)
        trend_strength = adx(df, 14).iloc[-1]
        if f.iloc[-1] > s.iloc[-1]:
            desired = 1
        elif f.iloc[-1] < s.iloc[-1]:
            desired = -1
        else:
            desired = 0
        # Weak trend: don't open new positions, but let winners ride.
        if trend_strength < adx_min and desired != current:
            return 0
        return desired


class RSIMeanReversion(Strategy):
    """RSI + Bollinger mean-reversion for ranging markets.

    Long on oversold at the lower band, short on overbought at the upper band,
    exit when price reverts to the middle band. A long-EMA hard filter blocks
    counter-trend entries in strong trends (mean reversion's failure mode)."""

    name = "rsi_meanrev"
    label = "RSI Mean-Reversion"
    trailing = False

    def min_bars(self) -> int:
        return 60  # BB(20) + RSI(14) settle far faster than a 55-EMA

    def signal(self, df, current):
        length = self.p("bb_length", 20)
        rsi_buy = self.p("rsi_buy", 30)
        rsi_sell = self.p("rsi_sell", 70)
        close = df["close"]
        mid, upper, lower = bollinger(close, length, self.p("bb_mult", 2.0))
        r = rsi(close, self.p("rsi_length", 14)).iloc[-1]
        c = close.iloc[-1]
        strong_trend = adx(df, 14).iloc[-1] > self.p("adx_max", 30)

        if current == 1 and c >= mid.iloc[-1]:
            return 0  # reverted to mean, take profit
        if current == -1 and c <= mid.iloc[-1]:
            return 0
        if current != 0:
            return current

        if strong_trend:
            return 0  # never fade a strong trend
        if c <= lower.iloc[-1] and r <= rsi_buy:
            return 1
        if c >= upper.iloc[-1] and r >= rsi_sell:
            return -1
        return 0


class DonchianBreakout(Strategy):
    """Turtle-style channel breakout: enter on a new N-bar extreme, exit on
    the opposite M-bar extreme (M < N)."""

    name = "donchian"
    label = "Donchian Breakout"
    trailing = True

    def signal(self, df, current):
        entry_n = self.p("entry", 20)
        exit_n = self.p("exit", 10)
        high, low, close = df["high"], df["low"], df["close"]
        c = close.iloc[-1]
        entry_hi = high.iloc[-entry_n - 1 : -1].max()
        entry_lo = low.iloc[-entry_n - 1 : -1].min()
        exit_hi = high.iloc[-exit_n - 1 : -1].max()
        exit_lo = low.iloc[-exit_n - 1 : -1].min()

        if current == 1:
            return 0 if c < exit_lo else 1
        if current == -1:
            return 0 if c > exit_hi else -1
        if c > entry_hi:
            return 1
        if c < entry_lo:
            return -1
        return 0


class AutoRegime(Strategy):
    """Regime switcher (recommended default).

    Research is clear that the market regime decides which family wins:
    trending -> trend-following, ranging -> mean-reversion. ADX measures the
    regime; this routes each symbol to the right strategy bar by bar."""

    name = "auto"
    label = "Auto (Regime Switch)"

    def __init__(self, params=None):
        super().__init__(params)
        self.trend = EMATrend(params)
        self.meanrev = RSIMeanReversion(params)
        self._last_regime = "trend"

    @property
    def trailing(self):  # type: ignore[override]
        return self._last_regime == "trend"

    def min_bars(self) -> int:
        return max(self.trend.min_bars(), self.meanrev.min_bars())

    def signal(self, df, current):
        strength = adx(df, 14).iloc[-1]
        # Hysteresis so the regime doesn't flap on the boundary.
        if strength >= self.p("adx_trend", 25):
            self._last_regime = "trend"
        elif strength <= self.p("adx_range", 20):
            self._last_regime = "range"
        # Bollinger band-width percentile is a LEADING regime read where ADX
        # lags: bottom-quintile width (a squeeze) is the pre-breakout state,
        # and fading a band touch there is mean-reversion's worst trade.
        if self._last_regime == "range" and current == 0:
            mid, upper, lower = bollinger(df["close"], 20, self.p("bb_mult", 2.0))
            bw = ((upper - lower) / mid).dropna()
            if len(bw) >= 40:
                window = bw.iloc[-100:]
                rank = float((window <= bw.iloc[-1]).mean())
                if rank < self.p("squeeze_pctile", 0.20):
                    return 0  # squeeze: stand aside until the break declares itself
        active = self.trend if self._last_regime == "trend" else self.meanrev
        return active.signal(df, current)


STRATEGIES = {cls.name: cls for cls in (AutoRegime, EMATrend, RSIMeanReversion, DonchianBreakout)}


def make_strategy(name: str, params: dict | None = None) -> Strategy:
    cls = STRATEGIES.get(name, AutoRegime)
    return cls(params)
