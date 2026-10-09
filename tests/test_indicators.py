import numpy as np
import pandas as pd
import pytest

from app import indicators as ind


def make_df(closes, start="2026-10-08 09:15", volume=1000.0, days=1):
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz="Asia/Kolkata")
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.5, "low": np.minimum(o, c) - 0.5,
                         "close": c, "volume": volume}, index=idx)


def test_ema_matches_recursive_definition():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = ind.ema(s, 3)
    k = 2 / (3 + 1)
    expect = [1.0]
    for v in s[1:]:
        expect.append(v * k + expect[-1] * (1 - k))
    assert np.allclose(out, expect)


def test_vwap_resets_each_session_and_handles_zero_volume():
    day1 = make_df([100, 101, 102], start="2026-10-07 09:15")
    day2 = make_df([200, 201, 202], start="2026-10-08 09:15")
    df = pd.concat([day1, day2])
    v = ind.vwap(df)
    tp0 = (df.iloc[3]["high"] + df.iloc[3]["low"] + df.iloc[3]["close"]) / 3
    assert v.iloc[3] == pytest.approx(tp0)  # first candle of day 2 starts fresh

    idx_df = df.assign(volume=0.0)  # index candles carry no volume
    assert ind.vwap(idx_df).notna().all()


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert ind.rsi(up).iloc[-1] == pytest.approx(100)
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert ind.rsi(down).iloc[-1] == pytest.approx(0)


def test_supertrend_follows_trend():
    rising = make_df(np.linspace(100, 160, 60))
    st = ind.supertrend(rising)
    assert st["st_dir"].iloc[-1] == 1
    assert st["st"].iloc[-1] < rising["close"].iloc[-1]

    falling = make_df(np.linspace(160, 100, 60))
    st = ind.supertrend(falling)
    assert st["st_dir"].iloc[-1] == -1
    assert st["st"].iloc[-1] > falling["close"].iloc[-1]


def test_cpr_levels_classic_formulas():
    lv = ind.cpr_levels(110, 90, 105)
    p = (110 + 90 + 105) / 3
    assert lv["P"] == pytest.approx(p)
    assert lv["BC"] == pytest.approx(100)
    assert lv["TC"] == pytest.approx(2 * p - 100)
    assert lv["R1"] == pytest.approx(2 * p - 90)
    assert lv["S1"] == pytest.approx(2 * p - 110)
    assert lv["TC"] >= lv["BC"]
    assert lv["S3"] < lv["S2"] < lv["S1"] < lv["BC"] <= lv["P"] <= lv["TC"] < lv["R1"] < lv["R2"] < lv["R3"]


def test_resample_15m_aligns_to_915():
    df = make_df(np.arange(12, dtype=float) + 100)
    r = ind.resample(df, "15min")
    assert r.index[0].strftime("%H:%M") == "09:15"
    assert r.index[1].strftime("%H:%M") == "09:30"
    assert r.iloc[0]["high"] == df.iloc[:3]["high"].max()
