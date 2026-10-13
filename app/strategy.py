"""Signal engine implementing the "Intraday Options Indicator Setup" rules.

Call buy needs ALL of:
  1. price above VWAP            2. EMA 9 above EMA 21       3. Supertrend (10,3) green
  4. RSI(14) >= 60               5. strong candle breaks above a CPR/pivot level
                                    (with volume >= 1.5x its 20-MA for stocks)
  6. 15-minute trend agrees
Put buy is the mirror image. Signals are read from the underlying on 5-minute candles.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import Settings
from .market import Instrument, is_expiry_day, session_phase, strike_step

LEVEL_ORDER = ["S3", "S2", "S1", "BC", "P", "TC", "R1", "R2", "R3"]
BREAKOUT_LOOKBACK = 3  # completed candles in which a level break still counts


@dataclass
class Context:
    """Inputs from outside the chart."""
    vix: float | None = None
    chain: dict | None = None
    losses_today: int = 0
    pnl_today: float = 0.0


def _completed(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Drop the still-forming candle: signals are confirmed on candle close."""
    if len(df) and df.index[-1] + timedelta(minutes=5) > now:
        return df.iloc[:-1]
    return df


def _strong(row: pd.Series, bullish: bool) -> bool:
    rng = row["high"] - row["low"]
    if rng <= 0:
        return False
    body = row["close"] - row["open"]
    return (body > 0 if bullish else body < 0) and abs(body) / rng >= 0.5


def find_breakout(df: pd.DataFrame, levels: dict[str, float], bullish: bool, is_stock: bool,
                  volume_mult: float) -> dict | None:
    """Most recent strong candle (within the lookback) that closed through a CPR/pivot level,
    with price still holding beyond it."""
    if len(df) < 2:
        return None
    last_close = df["close"].iloc[-1]
    names = LEVEL_ORDER if bullish else LEVEL_ORDER[::-1]
    for j in range(len(df) - 1, max(len(df) - 1 - BREAKOUT_LOOKBACK, 0), -1):
        row, prev = df.iloc[j], df.iloc[j - 1]
        same_day = df.index[j].date() == df.index[j - 1].date()
        ref = prev["close"] if same_day else row["open"]
        if not _strong(row, bullish):
            continue
        vol_ok = True
        if is_stock:
            vma = row.get("vol_ma20")
            vol_ok = bool(vma and not math.isnan(vma) and row["volume"] >= volume_mult * vma)
        for name in names:
            lvl = levels[name]
            crossed = ref < lvl < row["close"] if bullish else ref > lvl > row["close"]
            holding = last_close > lvl if bullish else last_close < lvl
            if crossed and holding:
                return {"level": name, "price": round(lvl, 2), "time": df.index[j].strftime("%H:%M"),
                        "volume_ok": vol_ok, "ok": vol_ok}
    return None


def trend_15m(df5: pd.DataFrame) -> dict:
    """15-minute trend from VWAP, EMA 21 and Supertrend, per the doc's second chart."""
    df15 = ind.resample(df5, "15min")
    if len(df15) < 12:
        return {"trend": "neutral", "df": df15}
    df15 = df15.assign(ema21=ind.ema(df15["close"], 21), vwap=ind.vwap(df15))
    st = ind.supertrend(df15, 10, 3)
    df15 = df15.assign(st=st["st"], st_dir=st["st_dir"])
    last = df15.iloc[-1]
    votes = [
        1 if last["close"] > last["ema21"] else -1,
        int(last["st_dir"]) or 0,
        1 if last["close"] > last["vwap"] else -1,
    ]
    score = sum(votes)
    trend = "up" if score >= 2 else "down" if score <= -2 else "neutral"
    return {"trend": trend, "df": df15, "close": float(last["close"]), "ema21": float(last["ema21"]),
            "st_dir": int(last["st_dir"]), "vwap": float(last["vwap"])}


def is_choppy(df: pd.DataFrame) -> tuple[bool, str]:
    """No-trade zone: at least two of price whipping around VWAP, EMAs flat/tangled,
    RSI stuck between 40 and 60."""
    tail = df.iloc[-12:]
    if len(tail) < 6:
        return False, ""
    side = np.sign(tail["close"] - tail["vwap"])
    crosses = int((side.diff().abs() > 0).sum())
    last = tail.iloc[-1]
    ema_gap = abs(last["ema9"] - last["ema21"]) / last["close"] * 100
    signs = []
    if crosses >= 3:
        signs.append(f"price crossed VWAP {crosses}x in the last hour")
    if ema_gap < 0.05:
        signs.append("EMAs flat and tangled")
    if 40 < last["rsi"] < 60:
        signs.append("RSI stuck between 40 and 60")
    if len(signs) >= 2:
        msg = "; ".join(signs)
        return True, msg[0].upper() + msg[1:]
    return False, ""


def _option_plan(direction: str, inst: Instrument, spot: float, entry: float, stop: float,
                 targets: list[float], ctx: Context, cfg: Settings, expiry_day: bool) -> dict:
    step = strike_step(inst, spot)
    atm = round(spot / step) * step
    chain = ctx.chain or {}
    if inst.kind == "index":
        itm = atm - step if direction == "CALL" else atm + step
        strikes = [itm, atm]
        rec = itm
        note = "ATM or 1 step ITM (delta 0.5-0.6)"
        delta = 0.58
    else:
        strikes = [atm]
        rec = atm
        note = "ATM only; avoid far OTM"
        delta = 0.5
    side = "ce" if direction == "CALL" else "pe"
    premium = None
    prem_map = chain.get("premium") or {}
    for k in (rec, *strikes):
        p = prem_map.get(k) or prem_map.get(float(k))
        if p and p.get(side):
            premium, rec = p[side], k
            break
    budget = cfg.capital * cfg.risk_per_trade_pct / 100
    size_notes = []
    if ctx.vix and ctx.vix > cfg.vix_high:
        budget /= 2
        size_notes.append(f"VIX {ctx.vix:.1f} > {cfg.vix_high:g}: half size")
    if expiry_day:
        budget /= 2
        size_notes.append("Expiry day: half size")
    plan = {
        "contract": f"{inst.symbol} {rec:g} {'CE' if direction == 'CALL' else 'PE'}",
        "strike": rec, "strike_note": note, "delta_est": delta,
        "premium": premium, "risk_budget": round(budget), "size_notes": size_notes,
        "lot_size": inst.lot_size,
    }
    if premium:
        under_risk = abs(entry - stop) * delta
        prem_cap = premium * cfg.premium_stop_pct / 100
        per_unit = min(under_risk, prem_cap)  # whichever stop comes first
        plan.update({
            "premium_stop": round(premium - per_unit, 2),
            "premium_targets": [round(premium + abs(t - entry) * delta, 2) for t in targets],
            "risk_per_unit": round(per_unit, 2),
        })
        if inst.lot_size:
            lots = int(budget // (per_unit * inst.lot_size)) if per_unit > 0 else 0
            one_lot = per_unit * inst.lot_size
            plan.update({"lots": lots, "max_loss": round(lots * one_lot), "one_lot_risk": round(one_lot)})
            if lots == 0:
                size_notes.append(f"1 lot risks Rs {one_lot:,.0f}, above the Rs {budget:,.0f} budget: skip or paper-trade")
    return plan


def evaluate(raw: pd.DataFrame, inst: Instrument, now: datetime, ctx: Context, cfg: Settings) -> dict:
    full = ind.compute_all(raw)
    df = _completed(full, now)
    if len(df) < 30:
        return {"symbol": inst.symbol, "status": "ERROR", "message": "Not enough candles"}

    today = df.index[-1].date()
    daily = ind.daily_ohlc(df)
    prev_days = daily[daily.index < today]
    if prev_days.empty:
        return {"symbol": inst.symbol, "status": "ERROR", "message": "Previous session missing for CPR"}
    p = prev_days.iloc[-1]
    levels = ind.cpr_levels(p["high"], p["low"], p["close"])

    last = df.iloc[-1]
    prev = df.iloc[-2]
    is_stock = inst.kind == "stock"
    tf = trend_15m(df)
    bo_bull = find_breakout(df, levels, True, is_stock, cfg.volume_mult)
    bo_bear = find_breakout(df, levels, False, is_stock, cfg.volume_mult)

    def fmt(x: float) -> str:
        return f"{x:,.2f}"

    rsi_cross_up = prev["rsi"] < cfg.rsi_bull <= last["rsi"]
    rsi_cross_dn = prev["rsi"] > cfg.rsi_bear >= last["rsi"]
    checks = [
        {"key": "vwap", "label": "Price vs VWAP",
         "bull": last["close"] > last["vwap"], "bear": last["close"] < last["vwap"],
         "value": f"{fmt(last['close'])} vs {fmt(last['vwap'])}"},
        {"key": "ema", "label": "EMA 9 vs EMA 21",
         "bull": last["ema9"] > last["ema21"], "bear": last["ema9"] < last["ema21"],
         "value": f"{fmt(last['ema9'])} / {fmt(last['ema21'])}"},
        {"key": "supertrend", "label": "Supertrend (10,3)",
         "bull": last["st_dir"] == 1, "bear": last["st_dir"] == -1,
         "value": ("Green" if last["st_dir"] == 1 else "Red") + f" @ {fmt(last['st'])}"},
        {"key": "rsi", "label": f"RSI 14 ({cfg.rsi_bear:g}/{cfg.rsi_bull:g})",
         "bull": last["rsi"] >= cfg.rsi_bull, "bear": last["rsi"] <= cfg.rsi_bear,
         "value": f"{last['rsi']:.1f}" + (" crossed up 60" if rsi_cross_up else " crossed down 40" if rsi_cross_dn else "")},
        {"key": "breakout", "label": "CPR / pivot break" + (" + volume" if is_stock else ""),
         "bull": bool(bo_bull and bo_bull["ok"]), "bear": bool(bo_bear and bo_bear["ok"]),
         "value": _breakout_text(bo_bull, bo_bear)},
        {"key": "tf15", "label": "15-min trend agrees",
         "bull": tf["trend"] == "up", "bear": tf["trend"] == "down", "value": tf["trend"].capitalize()},
    ]
    for c in checks:
        c["bull"], c["bear"] = bool(c["bull"]), bool(c["bear"])
    bull = sum(c["bull"] for c in checks)
    bear = sum(c["bear"] for c in checks)

    phase, phase_msg = session_phase(now)
    expiry_day = is_expiry_day(inst, today)
    chop, chop_msg = is_choppy(df)
    loss_hit = ctx.losses_today >= cfg.max_losing_trades_per_day or (
        cfg.daily_loss_limit > 0 and -ctx.pnl_today >= cfg.daily_loss_limit)

    direction = "CALL" if bull == 6 else "PUT" if bear == 6 else None
    lean = "CALL" if bull > bear else "PUT" if bear > bull else None
    if direction:
        if phase == "closed":
            status, msg = "CLOSED", f"All conditions met for {direction} at last close (market closed)"
        elif loss_hit:
            status, msg = "LOCKED", "Daily loss limit reached: no new trades today"
        elif phase in ("prime", "afternoon"):
            status, msg = f"BUY_{direction}", f"All 6 conditions met: BUY {direction}"
        elif phase == "exit":
            status, msg = "EXIT_WINDOW", "Setup valid but it is past 2:30 PM: no fresh option buys"
        else:
            status, msg = "WAIT", f"{direction} setup valid but {phase_msg.lower()}"
    elif chop:
        status, msg = "NO_TRADE", f"No-trade zone: {chop_msg}"
    elif lean and max(bull, bear) >= 5:
        missing = [c["label"] for c in checks if not c["bull" if lean == "CALL" else "bear"]]
        status, msg = f"WATCH_{lean}", f"{lean} setup forming ({max(bull, bear)}/6). Missing: " + ", ".join(missing)
    else:
        status, msg = "NO_TRADE", "Conditions mixed: sitting out is a position"
    if phase == "closed" and not status.startswith("CLOSED"):
        msg = "Market closed. " + msg

    # trade plan for the active or forming direction
    plan_dir = direction or (lean if status.startswith("WATCH") else None)
    plan = None
    if plan_dir:
        entry = float(last["close"])
        swing_low, swing_high = ind.swing_points(df)
        st_line = float(last["st"])
        if plan_dir == "CALL":
            stop = st_line if st_line < entry else swing_low
            alt = swing_low
        else:
            stop = st_line if st_line > entry else swing_high
            alt = swing_high
        risk = abs(entry - stop)
        sign = 1 if plan_dir == "CALL" else -1
        targets = [round(entry + sign * risk * m, 2) for m in (1.5, 2.0)]
        plan = {
            "direction": plan_dir, "entry": round(entry, 2), "stop": round(stop, 2), "alt_stop": round(alt, 2),
            "risk_points": round(risk, 2), "targets": targets,
            "trail": "Trail stop with Supertrend once in profit; exit all by 2:30 PM",
            "option": _option_plan(plan_dir, inst, entry, entry, stop, targets, ctx, cfg, expiry_day),
        }

    warnings = _warnings(inst, levels, ctx, cfg, float(last["close"]), plan_dir, expiry_day)
    return {
        "symbol": inst.symbol, "kind": inst.kind, "status": status, "message": msg,
        "direction": direction, "lean": lean, "score": {"bull": bull, "bear": bear},
        "time": df.index[-1].strftime("%Y-%m-%d %H:%M"), "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "ltp": round(float(full["close"].iloc[-1]), 2),
        "change_pct": round((float(full["close"].iloc[-1]) / p["close"] - 1) * 100, 2),
        "prev_close": round(float(p["close"]), 2),
        "phase": phase, "phase_msg": phase_msg, "expiry_day": expiry_day,
        "checks": checks, "plan": plan, "warnings": warnings,
        "levels": {k: round(v, 2) for k, v in levels.items()},
        "cpr_narrow": levels["width_pct"] < cfg.narrow_cpr_pct,
        "rsi": round(float(last["rsi"]), 1), "st_dir": int(last["st_dir"]), "st": round(float(last["st"]), 2),
        "trend15": tf["trend"], "_frames": {"m5": full, "m15": tf["df"]},
    }


def _breakout_text(bull: dict | None, bear: dict | None) -> str:
    parts = []
    for tag, b in (("Up", bull), ("Down", bear)):
        if b:
            vol = "" if b["volume_ok"] else " (volume weak)"
            parts.append(f"{tag} through {b['level']} {b['price']:,.2f} at {b['time']}{vol}")
    return "; ".join(parts) or "No fresh level break"


def _warnings(inst: Instrument, levels: dict, ctx: Context, cfg: Settings, price: float,
              direction: str | None, expiry_day: bool) -> list[str]:
    w = []
    if ctx.vix is not None:
        if ctx.vix > cfg.vix_high:
            w.append(f"India VIX {ctx.vix:.1f} > {cfg.vix_high:g}: premiums expensive, whipsaws likely. Trade smaller.")
        elif ctx.vix < cfg.vix_low:
            w.append(f"India VIX {ctx.vix:.1f} < {cfg.vix_low:g}: slow moves, theta hurts option buyers.")
    if levels["width_pct"] < cfg.narrow_cpr_pct:
        w.append(f"Narrow CPR ({levels['width_pct']:.2f}%): trending day more likely.")
    if expiry_day:
        w.append("Expiry day: fast moves and brutal theta. Reduce size" + ("" if inst.kind == "index" else "; avoid stock options") + ".")
    if inst.kind == "index":
        w.append("Index volume is unreliable and ignored.")
    chain = ctx.chain
    if chain and direction:
        res, sup = chain.get("resistance"), chain.get("support")
        if direction == "CALL" and res and 0 <= (res - price) / price * 100 < 0.3:
            w.append(f"Price is near the highest call-OI strike {res:g} (likely ceiling).")
        if direction == "PUT" and sup and 0 <= (price - sup) / price * 100 < 0.3:
            w.append(f"Price is near the highest put-OI strike {sup:g} (likely floor).")
    if inst.kind == "stock":
        w.append("Stock options: check sector index direction and news/results today; trade only liquid F&O names.")
    return w
