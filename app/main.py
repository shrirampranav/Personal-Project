"""FastAPI server: REST + WebSocket API and the single-page dashboard."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, settings
from .engine import Engine, chart_payload, public
from .fundamentals import fundamentals
from .journal import Journal
from .market import INDICES
from .providers import ProviderError, make_option_chain, make_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


def build_engine() -> Engine:
    try:
        provider = make_provider()
    except ProviderError as e:
        log.error("%s; falling back to demo data", e)
        provider = make_provider("demo")
    if provider.name == "demo" and "REFRESH_SECONDS" not in os.environ:
        settings.refresh_seconds = 5  # demo replays a session quickly
    return Engine(provider, make_option_chain(provider), Journal(settings.db_path), settings)


engine: Engine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = build_engine()
    task = asyncio.create_task(engine.run())
    log.info("Provider: %s | watchlist: %s", engine.provider.name, ", ".join(settings.watchlist))
    yield
    task.cancel()


app = FastAPI(title="Intraday Options Signal Desk", lifespan=lifespan)
STATIC = ROOT / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _engine() -> Engine:
    if engine is None:
        raise HTTPException(503, "engine not started")
    return engine


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    e = _engine()
    return {
        "provider": e.provider.name,
        "option_chain": getattr(e.chain_provider, "name", "off"),
        "watchlist": settings.watchlist, "indices": list(INDICES),
        "capital": settings.capital, "risk_per_trade_pct": settings.risk_per_trade_pct,
        "max_losing_trades": settings.max_losing_trades_per_day, "premium_stop_pct": settings.premium_stop_pct,
        "refresh_seconds": settings.refresh_seconds, "telegram": bool(settings.telegram_token),
    }


@app.get("/api/scan")
def scan() -> dict:
    e = _engine()
    return {"rows": e.summary(), "vix": e.vix, "last_scan": e.last_scan, "provider": e.provider.name}


@app.get("/api/analysis/{symbol}")
async def analysis(symbol: str) -> dict:
    e = _engine()
    try:
        result = await run_in_threadpool(e.analyze, symbol.upper(), True)
    except ProviderError as err:
        raise HTTPException(502, str(err))
    except Exception as err:  # unexpected data-source reply; show the reason in the UI
        log.exception("analysis %s failed", symbol)
        raise HTTPException(502, f"Data source error: {err}")
    if result.get("status") == "ERROR":
        raise HTTPException(422, result.get("message"))
    return public(result) | {"chart": chart_payload(result)}


@app.get("/api/fundamentals/{symbol}")
async def get_fundamentals(symbol: str) -> dict:
    return await run_in_threadpool(fundamentals, symbol)


@app.get("/api/alerts")
def alerts() -> list[dict]:
    return list(_engine().alerts)


class WatchlistIn(BaseModel):
    symbols: list[str]


@app.post("/api/watchlist")
def set_watchlist(body: WatchlistIn) -> dict:
    syms = [s.strip().upper() for s in body.symbols if s.strip()]
    if not syms:
        raise HTTPException(400, "watchlist cannot be empty")
    settings.watchlist = list(dict.fromkeys(syms))[:30]
    return {"watchlist": settings.watchlist}


class TradeIn(BaseModel):
    symbol: str
    direction: str = Field(pattern="^(CALL|PUT)$")
    contract: str | None = None
    reason: str | None = None
    entry_underlying: float | None = None
    stop_underlying: float | None = None
    target_underlying: float | None = None
    entry_premium: float | None = None
    lots: int = 1
    lot_size: int = 1
    notes: str | None = None


class CloseIn(BaseModel):
    exit_premium: float | None = None
    exit_underlying: float | None = None
    reason: str = ""


@app.get("/api/trades")
def trades() -> dict:
    j = _engine().journal
    return {"trades": j.all(), "stats": j.stats()}


@app.post("/api/trades")
def open_trade(body: TradeIn) -> dict:
    return _engine().journal.open_trade(body.model_dump() | {"symbol": body.symbol.upper()})


@app.post("/api/trades/{tid}/close")
def close_trade(tid: int, body: CloseIn) -> dict:
    try:
        return _engine().journal.close_trade(tid, body.exit_premium, body.exit_underlying, body.reason)
    except KeyError:
        raise HTTPException(404, "trade not found")


@app.delete("/api/trades/{tid}")
def delete_trade(tid: int) -> dict:
    _engine().journal.delete(tid)
    return {"ok": True}


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    e = _engine()
    await socket.accept()
    e.clients.add(socket)
    await socket.send_json({"type": "scan", "rows": e.summary(), "vix": e.vix, "last_scan": e.last_scan,
                            "provider": e.provider.name})
    try:
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        e.clients.discard(socket)
