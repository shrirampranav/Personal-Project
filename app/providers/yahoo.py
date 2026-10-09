"""Free intraday candles from Yahoo Finance's public chart endpoint (no API key).

Good for NIFTY/BANKNIFTY and NSE stocks; quotes can lag the exchange by a minute or so.
Use the Angel One provider for real-time data from your own broker account.
"""
from __future__ import annotations

from datetime import datetime

import httpx
import pandas as pd

from ..market import IST, instrument
from .base import CandleProvider, ProviderError, frame

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"}


class YahooProvider(CandleProvider):
    name = "yahoo"

    def __init__(self) -> None:
        self.client = httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True)

    def _chart(self, sym: str, interval: str, rng: str) -> dict:
        r = self.client.get(URL.format(sym=sym), params={"interval": interval, "range": rng})
        if r.status_code != 200:
            raise ProviderError(f"Yahoo {sym}: HTTP {r.status_code}")
        result = (r.json().get("chart") or {}).get("result")
        if not result:
            raise ProviderError(f"Yahoo {sym}: no data")
        return result[0]

    def candles(self, symbol: str, days: int = 5) -> pd.DataFrame:
        data = self._chart(instrument(symbol).yahoo, "5m", f"{max(days, 2)}d")
        ts = data.get("timestamp") or []
        q = data["indicators"]["quote"][0]
        rows = [
            (datetime.fromtimestamp(t, IST), q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i] or 0)
            for i, t in enumerate(ts)
        ]
        return frame(rows)

    def vix(self) -> float | None:
        try:
            return float(self._chart("^INDIAVIX", "1d", "5d")["meta"]["regularMarketPrice"])
        except (ProviderError, KeyError, TypeError, httpx.HTTPError):
            return None

