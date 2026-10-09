"""Fundamental snapshot for NSE stocks via Yahoo Finance (yfinance), with a simple checklist score."""
from __future__ import annotations

import time
from datetime import datetime

from .market import IST, instrument

_cache: dict[str, tuple[float, dict]] = {}
TTL = 6 * 3600

# (field, label, test, format)
CHECKS = [
    ("trailingPE", "P/E below 40", lambda v: 0 < v < 40, "{:.1f}"),
    ("returnOnEquity", "ROE above 15%", lambda v: v > 0.15, "{:.1%}"),
    ("debtToEquity", "Debt/Equity below 100%", lambda v: v < 100, "{:.0f}%"),
    ("profitMargins", "Net margin above 10%", lambda v: v > 0.10, "{:.1%}"),
    ("revenueGrowth", "Revenue growing", lambda v: v > 0, "{:.1%}"),
    ("earningsGrowth", "Earnings growing", lambda v: v > 0, "{:.1%}"),
]


def fundamentals(symbol: str) -> dict:
    inst = instrument(symbol)
    if inst.kind == "index":
        return {"symbol": inst.symbol, "available": False, "message": "Fundamentals apply to stocks, not indices."}
    hit = _cache.get(inst.symbol)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        import yfinance as yf
        t = yf.Ticker(inst.yahoo)
        info = t.info or {}
    except Exception as e:  # network, rate limit or parsing errors from yfinance
        return {"symbol": inst.symbol, "available": False, "message": f"Fundamentals unavailable: {e}"}
    if not info or info.get("quoteType") is None:
        return {"symbol": inst.symbol, "available": False, "message": "No fundamental data returned"}

    checks = []
    for key, label, test, fmt in CHECKS:
        v = info.get(key)
        if isinstance(v, (int, float)):
            checks.append({"label": label, "value": fmt.format(v), "pass": bool(test(v))})
        else:
            checks.append({"label": label, "value": "n/a", "pass": None})
    scored = [c for c in checks if c["pass"] is not None]
    score = sum(c["pass"] for c in scored)

    events = []
    try:
        cal = t.calendar or {}
        for d in cal.get("Earnings Date", []) or []:
            days = (d - datetime.now(IST).date()).days
            if 0 <= days <= 14:
                events.append(f"Results due {d:%d %b} ({days} days): option IV may spike then crush.")
    except Exception:
        pass

    result = {
        "symbol": inst.symbol, "available": True,
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"), "industry": info.get("industry"),
        "market_cap_cr": round(info["marketCap"] / 1e7) if info.get("marketCap") else None,
        "pb": info.get("priceToBook"), "dividend_yield": info.get("dividendYield"),
        "high_52w": info.get("fiftyTwoWeekHigh"), "low_52w": info.get("fiftyTwoWeekLow"),
        "checks": checks, "score": score, "out_of": len(scored),
        "verdict": "Strong" if scored and score >= len(scored) * 0.75 else "Average" if score >= len(scored) / 2 else "Weak",
        "events": events,
    }
    _cache[inst.symbol] = (time.time(), result)
    return result
