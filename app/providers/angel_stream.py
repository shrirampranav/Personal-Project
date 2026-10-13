"""Live tick feed from Angel One SmartStream (WebSocket V2), the same feed the Angel One app uses.

Prices are pushed on every trade, so the dashboard updates immediately instead of
waiting for the next candle poll. Signals still use completed 5-minute candles.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from websockets.asyncio.client import connect

from ..config import settings
from .angelone import AngelOneProvider

log = logging.getLogger(__name__)

URL = "wss://smartapisocket.angelone.in/smart-stream"
NSE_CM = 1          # exchangeType for NSE cash market (stocks and indices)
MODE_LTP = 1        # last traded price only: smallest, fastest packet
HEARTBEAT = 25      # seconds; server drops idle connections after ~30s
RESYNC = 3          # seconds between checks for watchlist changes


def parse_ltp(packet: bytes) -> tuple[str, float, int] | None:
    """Decode a SmartStream binary packet (little-endian) into (token, ltp, exchange_ts_ms).

    Layout: [0] mode, [1] exchange type, [2:27] token (null padded),
    [27:35] sequence no., [35:43] exchange timestamp ms, [43:51] LTP in paise.
    """
    if len(packet) < 51:
        return None
    token = packet[2:27].split(b"\x00", 1)[0].decode("ascii", "ignore")
    ts = int.from_bytes(packet[35:43], "little", signed=True)
    ltp = int.from_bytes(packet[43:51], "little", signed=True) / 100
    if not token or ltp <= 0:
        return None
    return token, ltp, ts


class AngelTickStream:
    def __init__(self, provider: AngelOneProvider, on_tick: Callable[[str, float, int], None],
                 symbols: Callable[[], list[str]]) -> None:
        self.provider = provider
        self.on_tick = on_tick
        self.symbols = symbols
        self.by_token: dict[str, str] = {}
        self.connected = False

    async def _resolve(self, wanted: list[str]) -> dict[str, str]:
        """symbol -> token for the watchlist (scrip search runs in a thread; it is rate limited)."""
        out = {}
        for sym in wanted:
            try:
                token, _ = await asyncio.to_thread(self.provider._token, sym)
                out[sym] = token
            except Exception as e:  # unknown symbol: skip it, keep streaming the rest
                log.info("tick stream: cannot resolve %s: %s", sym, e)
        return out

    async def _subscribe(self, ws, tokens: list[str]) -> None:
        if tokens:
            await ws.send(json.dumps({
                "correlationID": "signaldesk",
                "action": 1,
                "params": {"mode": MODE_LTP, "tokenList": [{"exchangeType": NSE_CM, "tokens": tokens}]},
            }))

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT)
            await ws.send("ping")

    async def _sync(self, ws, subscribed: set[str]) -> None:
        while True:
            mapping = await self._resolve(list(self.symbols()))
            new = [t for t in mapping.values() if t not in subscribed]
            for sym, tok in mapping.items():
                self.by_token[tok] = sym
            await self._subscribe(ws, new)
            subscribed.update(new)
            await asyncio.sleep(RESYNC)

    async def run(self) -> None:
        delay, failed = 2, False
        while True:
            try:
                jwt, feed = await asyncio.to_thread(self.provider.stream_credentials, failed)
                headers = {
                    "Authorization": f"Bearer {jwt}",
                    "x-api-key": settings.angel_api_key,
                    "x-client-code": settings.angel_client_code,
                    "x-feed-token": feed,
                }
                async with connect(URL, additional_headers=headers, ping_interval=None, open_timeout=15) as ws:
                    self.connected = True
                    delay, failed = 2, False
                    log.info("Angel One live tick feed connected")
                    tasks = [asyncio.create_task(self._heartbeat(ws)), asyncio.create_task(self._sync(ws, set()))]
                    try:
                        async for msg in ws:
                            if isinstance(msg, bytes):
                                tick = parse_ltp(msg)
                                if tick and tick[0] in self.by_token:
                                    self.on_tick(self.by_token[tick[0]], tick[1], tick[2])
                    finally:
                        for t in tasks:
                            t.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network drop, auth refresh, server restart: reconnect
                log.warning("Angel One tick feed disconnected (%s); retrying in %ss", e, delay)
                failed = True
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
