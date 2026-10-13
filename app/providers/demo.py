"""Simulated market feed so the app can be used and tested without a broker login.

Outside market hours it replays a full trading session in a loop (one real second =
one simulated minute by default), so alerts can be watched at any time of day.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..market import IST, MARKET_OPEN, is_market_open, now_ist
from .base import CandleProvider, frame

BASE_PRICES = {"NIFTY": 25100.0, "BANKNIFTY": 56200.0, "FINNIFTY": 26800.0, "MIDCPNIFTY": 13200.0}
SESSION_MINUTES = 375  # 9:15 -> 15:30


def _seed(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class DemoProvider(CandleProvider):
    name = "demo"

    def __init__(self, replay_speed: float = 60.0, start: datetime | None = None):
        self.replay_speed = replay_speed  # simulated seconds per real second
        self._t0 = start or now_ist()

    # ---- clock -------------------------------------------------------------------
    def now(self) -> datetime:
        real = now_ist()
        if is_market_open(real):
            return real
        elapsed_min = (real - self._t0).total_seconds() * self.replay_speed / 60
        minute = int(elapsed_min) % (SESSION_MINUTES + 30)  # pause briefly after close
        day = self._session_day(real)
        open_dt = datetime.combine(day, MARKET_OPEN, IST)
        return open_dt + timedelta(minutes=min(minute, SESSION_MINUTES), seconds=int(elapsed_min * 60) % 60)

    @staticmethod
    def _session_day(real: datetime):
        d = real.date()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    # ---- data --------------------------------------------------------------------
    def _base(self, symbol: str) -> float:
        if symbol in BASE_PRICES:
            return BASE_PRICES[symbol]
        return float(200 + _seed(symbol) % 3000)

    def _minute_path(self, symbol: str, day, open_price: float) -> pd.DataFrame:
        rng = np.random.default_rng(_seed(symbol, day))
        vol_per_min = 0.00045
        # regime-switching drift so the day has trends and chop
        drift = np.zeros(SESSION_MINUTES)
        i = 0
        while i < SESSION_MINUTES:
            span = int(rng.integers(30, 90))
            drift[i:i + span] = rng.choice([-1.2, -0.6, 0, 0, 0.6, 1.2]) * vol_per_min * 0.5
            i += span
        rets = drift + rng.normal(0, vol_per_min, SESSION_MINUTES)
        closes = open_price * np.exp(np.cumsum(rets))
        opens = np.concatenate([[open_price], closes[:-1]])
        wick = np.abs(rng.normal(0, vol_per_min * 0.6, SESSION_MINUTES)) * closes
        highs = np.maximum(opens, closes) + wick
        lows = np.minimum(opens, closes) - wick * rng.uniform(0.5, 1.5, SESSION_MINUTES)
        is_index = symbol in BASE_PRICES
        base_vol = 0 if is_index else 20000 + _seed(symbol, "v") % 80000
        u = np.abs(np.arange(SESSION_MINUTES) - SESSION_MINUTES / 2) / (SESSION_MINUTES / 2)
        volume = base_vol * (0.6 + u ** 2) * (1 + 40 * np.abs(rets)) * rng.uniform(0.6, 1.4, SESSION_MINUTES)
        start = datetime.combine(day, MARKET_OPEN, IST)
        idx = pd.date_range(start, periods=SESSION_MINUTES, freq="1min")
        return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume}, index=idx)

    def candles(self, symbol: str, days: int = 5) -> pd.DataFrame:
        symbol = symbol.upper()
        now = self.now()
        sessions = []
        d = self._session_day(now)
        while len(sessions) < days:
            sessions.append(d)
            d -= timedelta(days=1)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
        sessions.reverse()
        price = self._base(symbol)
        frames = []
        for day in sessions:
            gap = np.random.default_rng(_seed(symbol, day, "gap")).normal(0, 0.004)
            path = self._minute_path(symbol, day, price * (1 + gap))
            price = float(path["close"].iloc[-1])
            if day == sessions[-1]:
                path = path[path.index <= now]
            frames.append(path)
        minute = pd.concat(frames)
        five = minute.resample("5min", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        rows = list(five.itertuples(name=None))
        return frame(rows)

    def vix(self) -> float | None:
        d = self._session_day(self.now())
        return round(11 + _seed("vix", d) % 900 / 100, 2)



class DemoTickStream:
    """Simulated tick feed: small moves around each symbol's latest candle close."""

    def __init__(self, on_tick, symbols, last_price, every: float = 0.4) -> None:
        self.on_tick, self.symbols, self.last_price, self.every = on_tick, symbols, last_price, every
        self.connected = True
        self._rng = np.random.default_rng()

    async def run(self) -> None:
        import asyncio
        drift: dict[str, float] = {}
        while True:
            await asyncio.sleep(self.every)
            for sym in list(self.symbols()):
                base = self.last_price(sym)
                if not base or self._rng.random() < 0.4:  # not every symbol trades every interval
                    continue
                drift[sym] = 0.8 * drift.get(sym, 0.0) + self._rng.normal(0, 0.0002)
                # ts 0: the demo chart replays a simulated session, so ticks only move the forming candle
                self.on_tick(sym, round(base * (1 + drift[sym]), 2), 0)
