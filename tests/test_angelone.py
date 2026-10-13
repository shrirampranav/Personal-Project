"""Angel One provider against a fake SmartAPI: rate limits, non-JSON replies, session expiry."""
import httpx
import pytest

from app.config import settings
from app.providers import angelone
from app.providers.base import ProviderError

CANDLE_ROWS = [[f"2026-10-12T{h:02d}:{m:02d}:00+05:30", 100, 101, 99, 100.5, 1000]
               for h in (9, 10) for m in (15, 20, 25)]


@pytest.fixture
def provider(monkeypatch):
    for k, v in {"angel_api_key": "k", "angel_client_code": "c", "angel_pin": "1234",
                 "angel_totp_secret": "JBSWY3DPEHPK3PXP"}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(angelone.time, "sleep", lambda s: None)

    def make(handler):
        p = angelone.AngelOneProvider()
        p.client = httpx.Client(base_url=angelone.BASE, transport=httpx.MockTransport(handler))
        return p
    return make


def login_ok():
    return httpx.Response(200, json={"status": True, "data": {"jwtToken": "jwt"}})


def test_retries_plain_text_rate_limit_then_succeeds(provider):
    calls = {"candles": 0}

    def handler(req: httpx.Request):
        if req.url.path == angelone.LOGIN:
            return login_ok()
        calls["candles"] += 1
        if calls["candles"] <= 2:
            return httpx.Response(403, text="Access denied because of exceeding access rate")
        return httpx.Response(200, json={"status": True, "data": CANDLE_ROWS})

    df = provider(handler).candles("NIFTY")
    assert len(df) == len(CANDLE_ROWS)
    assert calls["candles"] == 3


def test_gives_up_with_readable_error(provider):
    def handler(req):
        if req.url.path == angelone.LOGIN:
            return login_ok()
        return httpx.Response(200, text="Access denied because of exceeding access rate")

    with pytest.raises(ProviderError, match="exceeding access rate"):
        provider(handler).candles("NIFTY")


def test_relogin_on_expired_token(provider):
    state = {"logins": 0, "candles": 0}

    def handler(req):
        if req.url.path == angelone.LOGIN:
            state["logins"] += 1
            return login_ok()
        state["candles"] += 1
        if state["candles"] == 1:
            return httpx.Response(200, json={"status": False, "errorcode": "AG8001", "message": "Invalid Token"})
        return httpx.Response(200, json={"status": True, "data": CANDLE_ROWS})

    provider(handler).candles("BANKNIFTY")
    assert state["logins"] == 2


def test_candles_are_cached_briefly(provider):
    state = {"candles": 0}

    def handler(req):
        if req.url.path == angelone.LOGIN:
            return login_ok()
        state["candles"] += 1
        return httpx.Response(200, json={"status": True, "data": CANDLE_ROWS})

    p = provider(handler)
    p.candles("NIFTY")
    p.candles("nifty")
    assert state["candles"] == 1


def test_login_failure_message(provider):
    def handler(req):
        return httpx.Response(200, json={"status": False, "message": "Invalid totp"})

    with pytest.raises(ProviderError, match="Invalid totp"):
        provider(handler).candles("NIFTY")
