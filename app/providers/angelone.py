"""Real-time candles from Angel One SmartAPI (your own broker account).

Needs a SmartAPI key from https://smartapi.angelone.in plus your client code, MPIN
and the TOTP secret shown when you enable TOTP. The app only reads market data;
it never places orders.
"""
from __future__ import annotations

import threading
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
        self._tokens: dict[str, tuple[str, str]] = dict(INDEX_TOKENS)
        self._lock = threading.Lock()

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

    def _login(self) -> None:
        body = {
            "clientcode": settings.angel_client_code,
            "password": settings.angel_pin,
            "totp": pyotp.TOTP(settings.angel_totp_secret).now(),
        }
        self._jwt = None
        r = self.client.post(LOGIN, json=body, headers=self._headers())
        data = r.json() if r.content else {}
        if not data.get("status"):
            raise ProviderError(f"Angel One login failed: {data.get('message') or r.status_code}")
        self._jwt = data["data"]["jwtToken"]

    def _post(self, path: str, body: dict) -> dict:
        with self._lock:
            if not self._jwt:
                self._login()
            for attempt in range(2):
                r = self.client.post(path, json=body, headers=self._headers())
                data = r.json() if r.content else {}
                expired = r.status_code in (401, 403) or data.get("errorcode") in ("AG8001", "AG8002", "AG8003")
                if expired and attempt == 0:
                    self._login()
                    continue
                if not data.get("status"):
                    raise ProviderError(f"Angel One {path}: {data.get('message') or r.status_code}")
                return data
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
        return frame(rows)

    def vix(self) -> float | None:
        token, name = INDEX_TOKENS["INDIAVIX"]
        try:
            data = self._post(LTP, {"exchange": "NSE", "tradingsymbol": name, "symboltoken": token})
            return float(data["data"]["ltp"])
        except (ProviderError, KeyError, TypeError, httpx.HTTPError):
            return None
