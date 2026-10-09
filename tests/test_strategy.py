from datetime import datetime

import numpy as np
import pytest
import pandas as pd

from app.config import Settings
from app.market import IST, instrument
from app.strategy import Context, evaluate, find_breakout
from app import indicators as ind

CFG = Settings()


def candles(closes, start, vol=None):
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz="Asia/Kolkata")
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    v = np.full(len(c), 1000.0) if vol is None else np.asarray(vol, dtype=float)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 1, "low": np.minimum(o, c) - 1,
                         "close": c, "volume": v}, index=idx)


def bullish_day(final_jump=60.0, vol=None, today_start="2026-10-08 09:15"):
    """Prev day ranges 24900-25100 and closes 25000 (P=25000, R1=25100).
    Today trends up from 24900 to just under R1, then a strong candle breaks R1."""
    t = np.linspace(0, 6 * np.pi, 75)
    prev = 25000 + 95 * np.sin(t)
    prev[-1] = 25000
    prev_df = candles(prev, "2026-10-07 09:15")
    prev_df.iloc[10, prev_df.columns.get_loc("high")] = 25100
    prev_df.iloc[30, prev_df.columns.get_loc("low")] = 24900
    today = np.concatenate([np.linspace(24900, 25090, 49), [25090 + final_jump]])
    today_df = candles(today, today_start, vol)
    return pd.concat([prev_df, today_df])


def mirror(df):
    m = df.copy()
    m["open"], m["close"] = 50000 - df["open"], 50000 - df["close"]
    m["high"], m["low"] = 50000 - df["low"], 50000 - df["high"]
    return m


AFTERNOON = datetime(2026, 10, 8, 13, 26, tzinfo=IST)


def test_all_six_conditions_give_buy_call():
    r = evaluate(bullish_day(), instrument("NIFTY"), AFTERNOON, Context(vix=14), CFG)
    assert r["score"]["bull"] == 6, r["checks"]
    assert r["status"] == "BUY_CALL"
    plan = r["plan"]
    assert plan["direction"] == "CALL"
    assert plan["stop"] < plan["entry"] < plan["targets"][0] < plan["targets"][1]
    risk = plan["entry"] - plan["stop"]
    assert plan["targets"][0] == pytest.approx(plan["entry"] + 1.5 * risk, abs=0.02)
    assert plan["option"]["contract"].endswith("CE")
    # index: 1 step ITM recommended
    assert plan["option"]["strike"] <= plan["entry"]


def test_mirror_gives_buy_put():
    r = evaluate(mirror(bullish_day()), instrument("NIFTY"), AFTERNOON, Context(vix=14), CFG)
    assert r["score"]["bear"] == 6, r["checks"]
    assert r["status"] == "BUY_PUT"
    assert r["plan"]["option"]["contract"].endswith("PE")


def test_no_fresh_entries_after_230():
    r = evaluate(bullish_day(), instrument("NIFTY"), datetime(2026, 10, 8, 14, 40, tzinfo=IST), Context(), CFG)
    assert r["status"] == "EXIT_WINDOW"


def test_midday_window_waits():
    # same setup, but the breakout candle closes at 11:55 (midday chop window)
    df = bullish_day(today_start="2026-10-08 07:45")
    r = evaluate(df, instrument("NIFTY"), datetime(2026, 10, 8, 11, 56, tzinfo=IST), Context(), CFG)
    assert r["status"] == "WAIT"


def test_daily_loss_limit_locks_new_trades():
    r = evaluate(bullish_day(), instrument("NIFTY"), AFTERNOON, Context(losses_today=2), CFG)
    assert r["status"] == "LOCKED"


def test_forming_candle_is_ignored():
    df = bullish_day()
    now = datetime(2026, 10, 8, 13, 22, tzinfo=IST)  # last candle (13:20) still forming
    r = evaluate(df, instrument("NIFTY"), now, Context(), CFG)
    assert r["status"] != "BUY_CALL"
    assert r["time"].endswith("13:15")


def test_stock_breakout_needs_volume():
    weak = evaluate(bullish_day(), instrument("RELIANCE"), AFTERNOON, Context(), CFG)
    assert not next(c for c in weak["checks"] if c["key"] == "breakout")["bull"]
    assert weak["status"] == "WATCH_CALL"

    vol = np.full(50, 1000.0)
    vol[-1] = 5000
    strong = evaluate(bullish_day(vol=vol), instrument("RELIANCE"), AFTERNOON, Context(), CFG)
    assert strong["status"] == "BUY_CALL"


def test_choppy_market_is_no_trade():
    t = np.arange(125)
    prices = 25000 + 6 * np.sin(t * 1.3)
    df = candles(prices, "2026-10-07 09:15")
    df.index = pd.DatetimeIndex(list(df.index[:75]) + list(pd.date_range("2026-10-08 09:15", periods=50, freq="5min", tz="Asia/Kolkata")))
    r = evaluate(df, instrument("NIFTY"), AFTERNOON, Context(), CFG)
    assert r["status"] == "NO_TRADE"
    assert r["plan"] is None


def test_high_vix_halves_risk_budget():
    lo = evaluate(bullish_day(), instrument("NIFTY"), AFTERNOON, Context(vix=14), CFG)
    hi = evaluate(bullish_day(), instrument("NIFTY"), AFTERNOON, Context(vix=22), CFG)
    assert hi["plan"]["option"]["risk_budget"] == lo["plan"]["option"]["risk_budget"] / 2
    assert any("VIX" in w for w in hi["warnings"])


def test_option_sizing_uses_chain_premium():
    chain = {"premium": {25050.0: {"ce": 120.0, "pe": 90.0}, 25100.0: {"ce": 80.0, "pe": 130.0}}}
    r = evaluate(bullish_day(), instrument("NIFTY"), AFTERNOON, Context(vix=14, chain=chain), CFG)
    o = r["plan"]["option"]
    assert o["premium"] in (120.0, 80.0)
    assert o["premium_stop"] >= o["premium"] * 0.70 - 1e-9  # never more than the 30% premium cap
    assert o["lots"] == int(o["risk_budget"] // (o["risk_per_unit"] * 65))


def test_breakout_requires_strong_candle():
    df = ind.compute_all(bullish_day(final_jump=12))  # small final candle: 25090 -> 25102
    lv = ind.cpr_levels(25100, 24900, 25000)
    # body 12 vs range 14: still strong by body ratio, so shrink body further with long wicks
    df.iloc[-1, df.columns.get_loc("high")] += 40
    assert find_breakout(df, lv, True, False, 1.5) is None
