"""Scanner loop: evaluates the watchlist, raises alerts and watches open paper trades."""
from __future__ import annotations

import asyncio
import logging
import math
from collections import deque
from datetime import datetime

import pandas as pd

from . import notify
from .config import Settings
from .journal import Journal
from .market import IST, instrument
from .providers import CandleProvider, ProviderError
from .strategy import Context, evaluate

log = logging.getLogger(__name__)
IST_OFFSET = 19800  # chart library renders UTC; shift so the axis shows IST


class Engine:
    def __init__(self, provider: CandleProvider, chain_provider, journal: Journal, cfg: Settings) -> None:
        self.provider = provider
        self.chain_provider = chain_provider
        self.journal = journal
        self.cfg = cfg
        self.results: dict[str, dict] = {}
        self.errors: dict[str, str] = {}
        self.alerts: deque[dict] = deque(maxlen=200)
        self.vix: float | None = None
        self.clients: set = set()
        self._last_status: dict[str, str] = {}
        self._position_alerts: set[tuple[int, str]] = set()
        self.last_scan: str | None = None

    # ---- analysis ---------------------------------------------------------------
    def _chain(self, symbol: str, spot: float) -> dict | None:
        if not self.chain_provider:
            return None
        if hasattr(self.chain_provider, "vix") and self.vix:
            self.chain_provider.vix = self.vix
        try:
            return self.chain_provider.chain(symbol, spot)
        except (ProviderError, Exception) as e:  # NSE blocks/rate limits are common; never fail the scan
            log.info("option chain %s unavailable: %s", symbol, e)
            return None

    def analyze(self, symbol: str, with_chain: bool = False) -> dict:
        inst = instrument(symbol)
        raw = self.provider.candles(inst.symbol)
        now = self.provider.now()
        losses, pnl = self.journal.today(now)
        ctx = Context(vix=self.vix, losses_today=losses, pnl_today=pnl)
        spot = float(raw["close"].iloc[-1])
        if inst.kind == "index" or with_chain:
            ctx.chain = self._chain(inst.symbol, spot)
        result = evaluate(raw, inst, now, ctx, self.cfg)
        if ctx.chain is None and result.get("plan"):
            ctx.chain = self._chain(inst.symbol, spot)
            if ctx.chain:
                result = evaluate(raw, inst, now, ctx, self.cfg)
        result["chain"] = jsonable(_public_chain(ctx.chain))
        result["vix"] = self.vix
        return result

    def scan_once(self) -> list[dict]:
        self.vix = self.provider.vix()
        for sym in list(self.cfg.watchlist):
            try:
                self.results[sym] = self.analyze(sym)
                self.errors.pop(sym, None)
            except Exception as e:  # keep scanning the rest of the watchlist
                log.warning("scan %s failed: %s", sym, e)
                self.errors[sym] = str(e)
        self.last_scan = self.provider.now().strftime("%H:%M:%S")
        new_alerts = self._signal_alerts() + self._position_checks()
        for a in new_alerts:
            self.alerts.appendleft(a)
            if a["level"] in ("buy", "exit"):
                notify.telegram(f"{a['title']}\n{a['message']}")
        return new_alerts

    # ---- alerts -----------------------------------------------------------------
    def _alert(self, level: str, symbol: str, title: str, message: str) -> dict:
        return {"level": level, "symbol": symbol, "title": title, "message": message,
                "time": self.provider.now().strftime("%H:%M:%S"), "ts": datetime.now(IST).isoformat()}

    def _signal_alerts(self) -> list[dict]:
        out = []
        for sym, r in self.results.items():
            status = r["status"]
            prev = self._last_status.get(sym)
            self._last_status[sym] = status
            if status == prev or prev is None and not status.startswith("BUY_"):
                continue
            if status.startswith("BUY_"):
                plan = r["plan"] or {}
                opt = plan.get("option") or {}
                msg = (f"{opt.get('contract', '')} | Spot {plan.get('entry')} SL {plan.get('stop')} "
                       f"T1 {plan['targets'][0]} T2 {plan['targets'][1]}")
                if opt.get("premium"):
                    msg += f" | Premium {opt['premium']} SL {opt.get('premium_stop')}"
                out.append(self._alert("buy", sym, f"{sym}: BUY {r['direction']}", msg))
            elif status.startswith("WATCH_"):
                out.append(self._alert("watch", sym, f"{sym}: {status.replace('_', ' ').title()}", r["message"]))
        return out

    def _position_checks(self) -> list[dict]:
        out = []
        for t in self.journal.open_trades():
            r = self.results.get(t["symbol"])
            if not r or r.get("status") == "ERROR":
                continue
            price, call = r["ltp"], t["direction"] == "CALL"
            reasons = []
            stop, target = t["stop_underlying"], t["target_underlying"]
            if stop and (price <= stop if call else price >= stop):
                reasons.append(("stop", f"Stop-loss hit at {price} (SL {stop}). Exit now."))
            if target and (price >= target if call else price <= target):
                reasons.append(("target", f"Target {target} reached. Book or trail with Supertrend ({r['st']})."))
            if r["st_dir"] == (-1 if call else 1):
                reasons.append(("st", f"Supertrend flipped {'red' if call else 'green'}: exit {t['direction']}."))
            if r["phase"] == "exit":
                reasons.append(("time", "2:30 PM: exit option buys (theta and late swings)."))
            for key, msg in reasons:
                if (t["id"], key) not in self._position_alerts:
                    self._position_alerts.add((t["id"], key))
                    out.append(self._alert("exit", t["symbol"], f"{t['symbol']} #{t['id']} {t['direction']}: exit signal", msg))
        return out

    # ---- views ------------------------------------------------------------------
    def summary(self) -> list[dict]:
        rows = []
        for sym in self.cfg.watchlist:
            r = self.results.get(sym)
            if sym in self.errors or not r:
                rows.append({"symbol": sym, "status": "ERROR", "message": self.errors.get(sym, "Loading...")})
                continue
            rows.append(jsonable({k: r.get(k) for k in ("symbol", "kind", "ltp", "change_pct", "status", "message", "score",
                                              "rsi", "trend15", "phase", "time", "expiry_day")}))
        return rows

    async def broadcast(self, payload: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                alerts = await loop.run_in_executor(None, self.scan_once)
                await self.broadcast({"type": "scan", "rows": self.summary(), "vix": self.vix,
                                      "last_scan": self.last_scan, "provider": self.provider.name})
                for a in alerts:
                    await self.broadcast({"type": "alert", "alert": a})
            except Exception:
                log.exception("scan loop error")
            await asyncio.sleep(self.cfg.refresh_seconds)


def _public_chain(chain: dict | None) -> dict | None:
    if not chain:
        return None
    return {k: v for k, v in chain.items() if k != "premium"} | {
        "premium": [{"strike": k, **v} for k, v in chain.get("premium", {}).items()]}


def _num(v) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 2)


def chart_payload(result: dict, sessions: int = 2) -> dict:
    """Series for the browser charts (last N sessions)."""
    m5: pd.DataFrame = result["_frames"]["m5"]
    days = sorted(set(m5.index.date))[-sessions:]
    m5 = m5[pd.Index(m5.index.date).isin(days)]
    t = [int(ts.timestamp()) + IST_OFFSET for ts in m5.index]
    candles = [{"time": ti, "open": _num(o), "high": _num(h), "low": _num(lo), "close": _num(c)}
               for ti, o, h, lo, c in zip(t, m5["open"], m5["high"], m5["low"], m5["close"])]

    def line(col: str) -> list[dict]:
        return [{"time": ti, "value": _num(v)} for ti, v in zip(t, m5[col]) if _num(v) is not None]

    st = [{"time": ti, "value": _num(v), "dir": int(d)} for ti, v, d in zip(t, m5["st"], m5["st_dir"]) if _num(v) is not None]
    vol = [{"time": ti, "value": float(v), "up": bool(c >= o)} for ti, v, c, o in zip(t, m5["volume"], m5["close"], m5["open"])]
    m15 = result["_frames"]["m15"]
    m15 = m15[pd.Index(m15.index.date).isin(days)]
    t15 = [int(ts.timestamp()) + IST_OFFSET for ts in m15.index]
    c15 = [{"time": ti, "open": _num(o), "high": _num(h), "low": _num(lo), "close": _num(c)}
           for ti, o, h, lo, c in zip(t15, m15["open"], m15["high"], m15["low"], m15["close"])]
    ema15 = [{"time": ti, "value": _num(v)} for ti, v in zip(t15, m15.get("ema21", [])) if _num(v) is not None]
    st15 = [{"time": ti, "value": _num(v), "dir": int(d)}
            for ti, v, d in zip(t15, m15.get("st", []), m15.get("st_dir", [])) if _num(v) is not None]
    vwap15 = [{"time": ti, "value": _num(v)} for ti, v in zip(t15, m15.get("vwap", [])) if _num(v) is not None]
    return {
        "candles": candles, "vwap": line("vwap"), "ema9": line("ema9"), "ema21": line("ema21"),
        "rsi": line("rsi"), "supertrend": st, "volume": vol, "vol_ma20": line("vol_ma20"),
        "m15": {"candles": c15, "ema21": ema15, "supertrend": st15, "vwap": vwap15},
    }


def jsonable(v):
    """Convert numpy/pandas scalars to native types for JSON."""
    if isinstance(v, dict):
        return {(k.item() if hasattr(k, "item") else k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def public(result: dict) -> dict:
    return jsonable({k: v for k, v in result.items() if not k.startswith("_")})
