"""Technical indicators used by the setup: VWAP, EMA, Supertrend, RSI, CPR, volume MA.

All functions take a DataFrame indexed by IST timestamps with columns
open, high, low, close, volume.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session-anchored VWAP. Index candles carry no volume, so fall back to an
    equal-weighted average of typical price, which is what charting platforms show."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date
    vol = df["volume"].astype(float)
    if vol.sum() <= 0:
        vol = pd.Series(1.0, index=df.index)
    pv = (tp * vol).groupby(day).cumsum()
    cv = vol.groupby(day).cumsum().replace(0, np.nan)
    return (pv / cv).ffill()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def atr(df: pd.DataFrame, period: int = 10) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Returns columns st (line) and st_dir (1 = green/up, -1 = red/down)."""
    a = atr(df, period).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    upper = hl2 + multiplier * a
    lower = hl2 - multiplier * a
    fu, fl = upper.copy(), lower.copy()
    line = np.full(n, np.nan)
    direction = np.zeros(n, dtype=int)
    for i in range(n):
        if np.isnan(a[i]):
            continue
        if i > 0 and not np.isnan(fu[i - 1]):
            fu[i] = upper[i] if upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1] else fu[i - 1]
            fl[i] = lower[i] if lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1] else fl[i - 1]
        prev_dir = direction[i - 1] if i > 0 and direction[i - 1] != 0 else 1
        if prev_dir == 1:
            direction[i] = -1 if close[i] < fl[i] else 1
        else:
            direction[i] = 1 if close[i] > fu[i] else -1
        line[i] = fl[i] if direction[i] == 1 else fu[i]
    return pd.DataFrame({"st": line, "st_dir": direction}, index=df.index)


def cpr_levels(prev_high: float, prev_low: float, prev_close: float) -> dict[str, float]:
    """Classic daily pivot points plus Central Pivot Range from the previous session."""
    p = (prev_high + prev_low + prev_close) / 3
    bc = (prev_high + prev_low) / 2
    tc = 2 * p - bc
    tc, bc = max(tc, bc), min(tc, bc)
    rng = prev_high - prev_low
    return {
        "R3": prev_high + 2 * (p - prev_low),
        "R2": p + rng,
        "R1": 2 * p - prev_low,
        "TC": tc,
        "P": p,
        "BC": bc,
        "S1": 2 * p - prev_high,
        "S2": p - rng,
        "S3": prev_low - 2 * (prev_high - p),
        "width_pct": (tc - bc) / p * 100 if p else 0.0,
    }


def daily_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(df.index.date)
    return pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})


def resample(df: pd.DataFrame, rule: str = "15min") -> pd.DataFrame:
    """Resample intraday candles, keeping NSE's 9:15 alignment."""
    out = df.resample(rule, origin="start_day", offset="15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])


def swing_points(df: pd.DataFrame, lookback: int = 10) -> tuple[float, float]:
    """Most recent swing low / high over the last `lookback` completed candles."""
    tail = df.iloc[-lookback - 1:-1] if len(df) > lookback else df
    return float(tail["low"].min()), float(tail["high"].max())


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["vwap"] = vwap(df)
    out["ema9"] = ema(df["close"], 9)
    out["ema21"] = ema(df["close"], 21)
    out["rsi"] = rsi(df["close"], 14)
    st = supertrend(df, 10, 3)
    out["st"] = st["st"]
    out["st_dir"] = st["st_dir"]
    out["vol_ma20"] = df["volume"].rolling(20, min_periods=5).mean()
    return out
