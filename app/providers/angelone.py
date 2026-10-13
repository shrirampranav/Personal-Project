"""Real-time candles from Angel One SmartAPI (your own broker account).

Needs a SmartAPI key from https://smartapi.angelone.in plus your client code, MPIN
and the TOTP secret shown when you enable TOTP. The app only reads market data;
it never places orders.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta

import httpx
import pandas as pd
import pyotp

from ..config import settings
from ..market import now_ist
from .base import CandleProvider, ProviderError, frame

BASE = "https://apiconnect.angelone.in"
LOGIN = "/rest/auth/angelbroking/user/v1/loginByPassword"
CANDLES = "/rest/secure/angelbroking/historical/v1/getCandleData"
SEARCH = "/rest/secure/angelbroking/order/v1/searchScrip"
LTP = "/rest/secure/angelbroking/order/v1/getLtpData"

# Minimum seconds between calls per endpoint; SmartAPI allows ~3 candle calls/sec
# and rejects bursts with a plain-text "exceeding access rate" reply.
MIN_INTERVAL = {CANDLES: 0.4, SEARCH: 1.1, LTP: 0.2, LOGIN: 1.1}
RETRIES = 3
CANDLE_TTL = 8  # seconds; the scanner and the chart view share one fetch

# Exchange tokens for NSE indices on SmartAPI
INDEX_TOKENS = {
    "NIFTY": ("99926000", "Nifty 50"),
    "BANKNIFTY": ("99926009", "Nifty Bank"),
    "FINNIFTY": ("99926037", "Nifty Fin Service"),
    "MIDCPNIFTY": ("99926074", "NIFTY MID SELECT"),
    "INDIAVIX": ("99926017", "India VIX"),
}


class AngelOneProvider(CandleProvider):
    name = "angelone"

    def __init__(self) -> None:
        missing = [k for k in ("angel_api_key", "angel_client_code", "angel_pin", "angel_totp_secret")
                   if not getattr(settings, k)]
        if missing:
            raise ProviderError("Angel One credentials missing in .env: " + ", ".join(m.upper() for m in missing))
        self.client = httpx.Client(base_url=BASE, timeout=20)
        self._jwt: str | None = None
        self._feed: str | None = None
        self._tokens: dict[str, tuple[str, str]] = dict(INDEX_TOKENS)
        self._lock = threading.Lock()
        self._last_call: dict[str, float] = {}
        self._candle_cache: dict[str, tuple[float, pd.DataFrame]] = {}

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": settings.angel_api_key,
        }
        if self._jwt:
            h["Authorization"] = f"Bearer {self._jwt}"
        return h

    def _throttle(self, path: str) -> None:
        wait = self._last_call.get(path, 0) + MIN_INTERVAL.get(path, 0.2) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call[path] = time.monotonic()

    def _send(self, path: str, body: dict) -> tuple[httpx.Response, dict | None]:
        """POST with throttling. Returns (response, json or None if the body was not JSON)."""
        self._throttle(path)
        r = self.client.post(path, json=body, headers=self._headers())
        try:
            return r, r.json()
        except ValueError:  # rate-limit and gateway errors come back as plain text
            return r, None

    @staticmethod
    def _rate_limited(r: httpx.Response, data: dict | None) -> bool:
        text = (data or {}).get("message") or ("" if data is not None else r.text)
        return r.status_code == 429 or data is None or "exceed" in str(text).lower()

    def _login(self) -> None:
        body = {
            "clientcode": settings.angel_client_code,
            "password": settings.angel_pin,
            "totp": pyotp.TOTP(settings.angel_totp_secret).now(),
        }
        self._jwt = None
        r, data = self._send(LOGIN, body)
        if not data or not data.get("status"):
            reason = (data or {}).get("message") or r.text[:120] or r.status_code
            raise ProviderError(f"Angel One login failed: {reason}")
        self._jwt = data["data"]["jwtToken"]
        self._feed = data["data"].get("feedToken")

    def stream_credentials(self, force: bool = False) -> tuple[str, str]:
        """(jwt, feed token) for the live WebSocket feed; logs in again when forced."""
        with self._lock:
            if force or not self._jwt or not self._feed:
                self._login()
            if not self._feed:
                raise ProviderError("Angel One login returned no feed token")
            return self._jwt, self._feed

    def _post(self, path: str, body: dict) -> dict:
        with self._lock:
            if not self._jwt:
                self._login()
            relogged = False
            for attempt in range(RETRIES + 1):
                r, data = self._send(path, body)
                expired = r.status_code in (401, 403) or (data or {}).get("errorcode") in ("AG8001", "AG8002", "AG8003")
                if expired and not relogged:
                    relogged = True
                    self._login()
                    continue
                if data and data.get("status"):
                    return data
                if self._rate_limited(r, data) and attempt < RETRIES:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                reason = (data or {}).get("message") or r.text[:120] or r.status_code
                raise ProviderError(f"Angel One {path.rsplit('/', 1)[-1]}: {reason}")
        raise ProviderError("Angel One request failed")

    def _token(self, symbol: str) -> tuple[str, str]:
        symbol = symbol.upper()
        if symbol not in self._tokens:
            data = self._post(SEARCH, {"exchange": "NSE", "searchscrip": symbol})
            match = next((s for s in data.get("data") or [] if s.get("tradingsymbol") == f"{symbol}-EQ"), None)
            if not match:
                raise ProviderError(f"Angel One: no NSE equity found for {symbol}")
            self._tokens[symbol] = (match["symboltoken"], match["tradingsymbol"])
        return self._tokens[symbol]

    def candles(self, symbol: str, days: int = 5) -> pd.DataFrame:
        symbol = symbol.upper()
        hit = self._candle_cache.get(symbol)
        if hit and time.monotonic() - hit[0] < CANDLE_TTL:
            return hit[1]
        token, _ = self._token(symbol)
        now = now_ist()
        body = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": (now - timedelta(days=days + 4)).strftime("%Y-%m-%d 09:15"),
            "todate": now.strftime("%Y-%m-%d %H:%M"),
        }
        rows = [tuple(r[:6]) for r in self._post(CANDLES, body).get("data") or []]
        df = frame(rows)
        self._candle_cache[symbol] = (time.monotonic(), df)
        return df

    def vix(self) -> float | None:
        token, name = INDEX_TOKENS["INDIAVIX"]
        try:
            data = self._post(LTP, {"exchange": "NSE", "tradingsymbol": name, "symboltoken": token})
            return float(data["data"]["ltp"])
        except (ProviderError, KeyError, TypeError, httpx.HTTPError):
            return None
