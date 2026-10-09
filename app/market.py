"""Instrument metadata, IST market clock and session windows."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
AVOID_UNTIL = time(9, 30)       # skip the first 15 minutes
BEST_WINDOW_END = time(11, 30)  # 9:30-11:30 best window
AFTERNOON_START = time(13, 0)   # breakouts after 1:00 PM
EXIT_BY = time(14, 30)          # exit all option buys by 2:30 PM


@dataclass(frozen=True)
class Instrument:
    symbol: str
    kind: str            # "index" or "stock"
    strike_step: float
    lot_size: int | None  # None = look up / enter manually
    yahoo: str
    nse_name: str
    expiry_weekday: int | None = None  # 0=Mon ... for weekly index expiry


# Lot sizes and expiry days are set by NSE/SEBI and change periodically.
# Verify against NSE before trading and edit here if needed.
INDICES: dict[str, Instrument] = {
    "NIFTY": Instrument("NIFTY", "index", 50, 65, "^NSEI", "NIFTY", expiry_weekday=1),
    "BANKNIFTY": Instrument("BANKNIFTY", "index", 100, 30, "^NSEBANK", "BANKNIFTY"),
    "FINNIFTY": Instrument("FINNIFTY", "index", 50, 60, "NIFTY_FIN_SERVICE.NS", "FINNIFTY"),
    "MIDCPNIFTY": Instrument("MIDCPNIFTY", "index", 25, 120, "NIFTY_MID_SELECT.NS", "MIDCPNIFTY"),
}


def instrument(symbol: str) -> Instrument:
    symbol = symbol.upper().strip()
    if symbol in INDICES:
        return INDICES[symbol]
    return Instrument(symbol, "stock", 0, None, f"{symbol}.NS", symbol)


def stock_strike_step(price: float) -> float:
    """Approximate NSE strike interval for stock options by price band."""
    for limit, step in ((100, 1), (250, 2.5), (500, 5), (1000, 10), (2500, 20), (5000, 50)):
        if price < limit:
            return step
    return 100


def strike_step(inst: Instrument, price: float) -> float:
    return inst.strike_step or stock_strike_step(price)


def now_ist() -> datetime:
    return datetime.now(IST)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5  # exchange holidays are not modelled


def is_market_open(ts: datetime | None = None) -> bool:
    ts = ts or now_ist()
    return is_trading_day(ts.date()) and MARKET_OPEN <= ts.time() <= MARKET_CLOSE


def last_weekday_of_month(d: date, weekday: int) -> date:
    import calendar
    last = calendar.monthrange(d.year, d.month)[1]
    day = date(d.year, d.month, last)
    while day.weekday() != weekday:
        day = day.replace(day=day.day - 1)
    return day


def is_expiry_day(inst: Instrument, d: date) -> bool:
    """Weekly expiry for NIFTY, monthly (last Tuesday) for other F&O contracts."""
    if inst.expiry_weekday is not None and d.weekday() == inst.expiry_weekday:
        return True
    return d == last_weekday_of_month(d, 1)


def session_phase(ts: datetime) -> tuple[str, str]:
    """Return (phase, message) for the doc's time rules."""
    t = ts.time()
    if not is_trading_day(ts.date()) or t < MARKET_OPEN or t > MARKET_CLOSE:
        return "closed", "Market closed"
    if t < AVOID_UNTIL:
        return "opening", "First 15 minutes: avoid entries (9:15-9:30)"
    if t <= BEST_WINDOW_END:
        return "prime", "Best window (9:30-11:30)"
    if t < AFTERNOON_START:
        return "midday", "Midday chop (11:30-1:00): outside preferred window"
    if t < EXIT_BY:
        return "afternoon", "Afternoon breakout window (1:00-2:30)"
    return "exit", "After 2:30 PM: exit option buys, no fresh entries"
