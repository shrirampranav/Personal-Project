"""Option chain: OI levels, PCR, max pain and ATM/ITM premiums.

Sources: NSE's public option-chain API (needs browser-like cookies, rate-limited) or a
synthetic chain for demo mode.
"""
from __future__ import annotations

import math
import threading
import time

import httpx

from ..market import instrument, strike_step
from .base import ProviderError

NSE = "https://www.nseindia.com"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{NSE}/option-chain",
}


def summarize(rows: list[dict], spot: float, symbol: str, expiry: str, source: str) -> dict:
    """rows: [{strike, ce_oi, pe_oi, ce_chg, pe_chg, ce_ltp, pe_ltp, ce_iv, pe_iv}] for one expiry."""
    if not rows:
        raise ProviderError("empty option chain")
    rows = sorted(rows, key=lambda r: r["strike"])
    ce_total = sum(r["ce_oi"] for r in rows)
    pe_total = sum(r["pe_oi"] for r in rows)
    resistance = max(rows, key=lambda r: r["ce_oi"])["strike"]
    support = max(rows, key=lambda r: r["pe_oi"])["strike"]
    strikes = [r["strike"] for r in rows]

    def pain(k: float) -> float:
        return sum(r["ce_oi"] * max(k - r["strike"], 0) + r["pe_oi"] * max(r["strike"] - k, 0) for r in rows)

    max_pain = min(strikes, key=pain)
    atm = min(strikes, key=lambda k: abs(k - spot))
    i = strikes.index(atm)
    by_strike = {r["strike"]: r for r in rows}
    near = rows[max(0, i - 8): i + 9]
    return {
        "symbol": symbol,
        "source": source,
        "expiry": expiry,
        "spot": spot,
        "pcr": round(pe_total / ce_total, 2) if ce_total else None,
        "resistance": resistance,  # highest call OI = likely ceiling
        "support": support,        # highest put OI = likely floor
        "max_pain": max_pain,
        "atm": atm,
        "ce_itm1": strikes[i - 1] if i > 0 else atm,
        "pe_itm1": strikes[i + 1] if i + 1 < len(strikes) else atm,
        "premium": {k: {"ce": by_strike[k]["ce_ltp"], "pe": by_strike[k]["pe_ltp"]} for k in strikes[max(0, i - 2): i + 3]},
        "chain": near,
    }


class NseOptionChain:
    name = "nse"

    def __init__(self, ttl: float = 60) -> None:
        self.client = httpx.Client(headers=NSE_HEADERS, timeout=15, follow_redirects=True)
        self.ttl = ttl
        self._cache: dict[str, tuple[float, dict]] = {}
        self._warm = 0.0
        self._lock = threading.Lock()

    def _warmup(self) -> None:
        if time.time() - self._warm > 300:
            self.client.get(f"{NSE}/option-chain")
            self._warm = time.time()

    def _get(self, path: str, params: dict) -> dict:
        self._warmup()
        r = self.client.get(f"{NSE}{path}", params=params)
        if r.status_code in (401, 403):
            self._warm = 0
            self._warmup()
            r = self.client.get(f"{NSE}{path}", params=params)
        if r.status_code != 200 or not r.content:
            raise ProviderError(f"NSE {path}: HTTP {r.status_code}")
        return r.json()

    def _fetch(self, symbol: str) -> tuple[list[dict], float, str]:
        inst = instrument(symbol)
        kind = "Indices" if inst.kind == "index" else "Equity"
        try:  # current API (2025+)
            info = self._get("/api/option-chain-contract-info", {"symbol": inst.nse_name})
            expiry = info["expiryDates"][0]
            data = self._get("/api/option-chain-v3", {"type": kind, "symbol": inst.nse_name, "expiry": expiry})
        except (ProviderError, KeyError, IndexError, ValueError):
            path = "/api/option-chain-indices" if inst.kind == "index" else "/api/option-chain-equities"
            data = self._get(path, {"symbol": inst.nse_name})
            expiry = data["records"]["expiryDates"][0]
        records = data.get("records") or {}
        spot = float(records.get("underlyingValue") or 0)
        rows = []
        for item in records.get("data") or data.get("filtered", {}).get("data") or []:
            exp = item.get("expiryDate") or item.get("expiryDates")
            if exp and exp != expiry:
                continue
            ce, pe = item.get("CE") or {}, item.get("PE") or {}
            rows.append({
                "strike": float(item["strikePrice"]),
                "ce_oi": float(ce.get("openInterest") or 0), "pe_oi": float(pe.get("openInterest") or 0),
                "ce_chg": float(ce.get("changeinOpenInterest") or 0), "pe_chg": float(pe.get("changeinOpenInterest") or 0),
                "ce_ltp": float(ce.get("lastPrice") or 0), "pe_ltp": float(pe.get("lastPrice") or 0),
                "ce_iv": float(ce.get("impliedVolatility") or 0), "pe_iv": float(pe.get("impliedVolatility") or 0),
            })
        return rows, spot, expiry

    def chain(self, symbol: str, spot: float) -> dict:
        symbol = symbol.upper()
        with self._lock:
            hit = self._cache.get(symbol)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
            rows, nse_spot, expiry = self._fetch(symbol)
            result = summarize(rows, nse_spot or spot, symbol, expiry, "NSE")
            self._cache[symbol] = (time.time(), result)
            return result


def _bs(spot: float, strike: float, t: float, vol: float, call: bool) -> float:
    if t <= 0 or vol <= 0:
        return max(spot - strike, 0) if call else max(strike - spot, 0)
    d1 = (math.log(spot / strike) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    return spot * n(d1) - strike * n(d2) if call else strike * n(-d2) - spot * n(-d1)


class DemoOptionChain:
    name = "demo"

    def __init__(self, vix: float = 14.0) -> None:
        self.vix = vix

    def chain(self, symbol: str, spot: float) -> dict:
        inst = instrument(symbol)
        step = strike_step(inst, spot)
        atm = round(spot / step) * step
        vol = self.vix / 100 * (1.0 if inst.kind == "index" else 1.8)
        t = 4 / 365
        skew = 0.7 + (sum(map(ord, symbol)) % 70) / 100  # vary PCR between symbols
        rows = []
        for k in range(-15, 16):
            strike = atm + k * step
            dist = (strike - spot) / (step * 6)
            ce_oi = 1e6 * math.exp(-((dist - 0.6) ** 2)) * (1.6 if k % 4 == 2 else 1)
            pe_oi = skew * 1e6 * math.exp(-((dist + 0.6) ** 2)) * (1.6 if k % 4 == 1 else 1)
            rows.append({
                "strike": strike, "ce_oi": round(ce_oi), "pe_oi": round(pe_oi), "ce_chg": 0.0, "pe_chg": 0.0,
                "ce_ltp": round(_bs(spot, strike, t, vol, True), 2), "pe_ltp": round(_bs(spot, strike, t, vol, False), 2),
                "ce_iv": round(vol * 100, 1), "pe_iv": round(vol * 100, 1),
            })
        return summarize(rows, spot, symbol.upper(), "synthetic", "Demo")
