from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from ..market import now_ist

COLUMNS = ["open", "high", "low", "close", "volume"]


class ProviderError(RuntimeError):
    pass


class CandleProvider(ABC):
    name = "base"

    def now(self) -> datetime:
        """Clock used for session rules. The demo provider overrides this to replay a session."""
        return now_ist()

    @abstractmethod
    def candles(self, symbol: str, days: int = 5) -> pd.DataFrame:
        """5-minute candles (IST index, columns open/high/low/close/volume) covering
        at least the previous session, so CPR can be computed."""

    @abstractmethod
    def vix(self) -> float | None:
        """Latest India VIX value."""


def frame(rows: list[tuple], tz: str = "Asia/Kolkata") -> pd.DataFrame:
    """Build a candle frame from (timestamp, o, h, l, c, v) tuples."""
    if not rows:
        raise ProviderError("no candles returned")
    df = pd.DataFrame(rows, columns=["ts", *COLUMNS])
    df["ts"] = pd.to_datetime(df["ts"])
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize(tz)
    else:
        df["ts"] = df["ts"].dt.tz_convert(tz)
    df = df.dropna(subset=["open", "high", "low", "close"]).set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df["volume"] = df["volume"].fillna(0).astype(float)
    return df
