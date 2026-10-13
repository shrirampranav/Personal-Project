"""Live tick feed: SmartStream packet decoding, the WebSocket client and engine fan-out."""
import asyncio
import json
import struct

import pytest
from websockets.asyncio.server import serve

from app.config import settings
from app.engine import Engine
from app.providers import angel_stream
from app.providers.angel_stream import AngelTickStream, parse_ltp


def packet(token: str, ltp_rupees: float, ts_ms: int = 1_760_000_000_000, mode: int = 1, exch: int = 1) -> bytes:
    """Build an LTP-mode SmartStream packet exactly as the server sends it."""
    tok = token.encode().ljust(25, b"\x00")
    return struct.pack("<bb25sqqq", mode, exch, tok, 42, ts_ms, round(ltp_rupees * 100))


def test_parse_ltp_decodes_price_in_paise():
    assert parse_ltp(packet("99926000", 25012.35)) == ("99926000", 25012.35, 1_760_000_000_000)
    assert parse_ltp(packet("2885", 1157.2))[1] == 1157.2


def test_parse_ltp_rejects_short_or_empty_packets():
    assert parse_ltp(b"\x01\x01short") is None
    assert parse_ltp(packet("", 100.0)) is None
    assert parse_ltp(packet("2885", 0)) is None


class FakeProvider:
    def __init__(self):
        self.forced = []

    def stream_credentials(self, force=False):
        self.forced.append(force)
        return "jwt-token", "feed-token"

    def _token(self, symbol):
        return {"NIFTY": ("99926000", "Nifty 50"), "RELIANCE": ("2885", "RELIANCE-EQ")}[symbol]


def test_stream_authenticates_subscribes_and_delivers_ticks(monkeypatch):
    monkeypatch.setattr(settings, "angel_api_key", "api-key")
    monkeypatch.setattr(settings, "angel_client_code", "C123")
    seen = {}

    async def server(ws):
        seen["headers"] = dict(ws.request.headers)
        seen["subscribe"] = json.loads(await ws.recv())
        await ws.send(packet("99926000", 25010.5))
        await ws.send(packet("2885", 1158.0))
        await ws.send(packet("999", 1.0))  # token we did not subscribe to: ignored
        await asyncio.sleep(0.2)

    async def main():
        ticks = []
        async with serve(server, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            monkeypatch.setattr(angel_stream, "URL", f"ws://127.0.0.1:{port}")
            prov = FakeProvider()
            stream = AngelTickStream(prov, lambda s, p, t: ticks.append((s, p)), lambda: ["NIFTY", "RELIANCE"])
            task = asyncio.create_task(stream.run())
            for _ in range(50):
                if len(ticks) >= 2:
                    break
                await asyncio.sleep(0.05)
            task.cancel()
        return ticks

    ticks = asyncio.run(main())
    assert ticks == [("NIFTY", 25010.5), ("RELIANCE", 1158.0)]
    h = seen["headers"]
    assert h["authorization"] == "Bearer jwt-token"
    assert h["x-api-key"] == "api-key" and h["x-client-code"] == "C123" and h["x-feed-token"] == "feed-token"
    sub = seen["subscribe"]
    assert sub["action"] == 1 and sub["params"]["mode"] == 1
    assert sorted(sub["params"]["tokenList"][0]["tokens"]) == ["2885", "99926000"]


class StubJournal:
    def open_trades(self):
        return []


def test_engine_uses_live_price_and_batches_ticks():
    eng = Engine(provider=None, chain_provider=None, journal=StubJournal(), cfg=settings)
    settings_watch = settings.watchlist
    try:
        settings.watchlist = ["NIFTY"]
        eng.results["NIFTY"] = {"symbol": "NIFTY", "ltp": 25000.0, "change_pct": 0.0, "prev_close": 25000.0,
                                "status": "NO_TRADE"}
        eng.on_tick("NIFTY", 25100.0, 0)
        row = eng.summary()[0]
        assert row["ltp"] == 25100.0 and row["change_pct"] == pytest.approx(0.4)

        sent = []

        class Client:
            async def send_json(self, payload):
                sent.append(payload)

        eng.clients.add(Client())

        async def run_once():
            task = asyncio.create_task(eng.flush_ticks(every=0.01))
            await asyncio.sleep(0.05)
            task.cancel()

        asyncio.run(run_once())
        assert sent and sent[0]["type"] == "ticks"
        assert sent[0]["ticks"]["NIFTY"] == {"ltp": 25100.0, "ts": 0, "prev_close": 25000.0}
    finally:
        settings.watchlist = settings_watch
