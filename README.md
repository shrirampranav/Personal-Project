# Options Signal Desk

A single-screen web app for intraday options traders. It watches NIFTY, BANKNIFTY and F&O stocks on 5-minute candles and raises **BUY CALL / BUY PUT / WATCH / EXIT** alerts using the rules in *Intraday Options Indicator Setup — Angel One*.

It only shows alerts. It never places orders.

## What it does

| Area | What you get |
|---|---|
| **Scanner** | Watchlist scanned every 30 s, with a signal for each symbol: BUY CALL, BUY PUT, WATCH, NO TRADE, WAIT or LOCKED |
| **Charts** | 5-min underlying chart with VWAP, EMA 9/21, Supertrend (10,3), CPR/pivots and max-OI lines, plus RSI 14 (40/60) and volume. A 15-min trend chart sits below it. |
| **Entry checklist** | The six conditions from the document, ticked live for calls and puts |
| **Trade plan** | Spot stop (Supertrend or swing), 1.5R and 2R targets, the strike (ITM/ATM for indices, ATM for stocks), premium stop (min of 30% and delta × spot risk), and lots sized to 1% of capital |
| **Market context** | India VIX, option chain PCR, max call/put OI (ceiling/floor), max pain and CPR width |
| **Fundamentals** | P/E, ROE, debt/equity, margins, growth, plus a warning when results are due |
| **Alerts** | In-app feed, browser notifications, a beep, and optional Telegram to your phone |
| **Paper-trade journal** | Log trades with one click; tracks win rate, average win/loss and expectancy toward the 20-30 trade test |
| **Exit alerts** | For open paper trades: stop hit, target hit, Supertrend flip, and the 2:30 PM exit |

## Rules encoded (from the document)

**BUY CALL requires all six conditions** on the last *completed* 5-min candle of the underlying:

1. Close above session VWAP
2. EMA 9 above EMA 21
3. Supertrend (10, 3) green
4. RSI 14 at or above 60
5. A strong candle (body ≥ 50% of range) breaks above a CPR or pivot level within the last 3 candles and price still holds above it. For stocks, volume must also be ≥ 1.5× its 20-period average.
6. The 15-min trend agrees (at least 2 of: close > EMA 21, Supertrend green, close > VWAP)

**BUY PUT** is the mirror image.

**Gates:**
- No entries 9:15-9:30 or 11:30-1:00 (WAIT).
- No fresh option buys after 2:30 PM.
- Locked after 2 losing trades in a day.

**No-trade zone:** when at least two of these hold:
- price crossing VWAP 3 or more times in an hour
- EMAs within 0.05% of each other
- RSI between 40 and 60

**Sizing:**
- Halved when VIX > 18.
- Halved on expiry day.
- Warning when VIX < 12 (theta risk) or price is near the max-OI strike.

Thresholds are in `app/config.py`. Lot sizes and expiry weekdays are in `app/market.py`; check them against NSE, because they change.

## Quick start

Needs Python 3.10 or newer.

```bash
# macOS / Linux
./run.sh
# Windows
run.bat
```

Open http://localhost:8000. The first run creates `.env` from `.env.example`.

### Choose a data source (`DATA_PROVIDER` in `.env`)

| Provider | Setup | Notes |
|---|---|---|
| `demo` | none | Simulated market. Outside market hours it replays a full session at 60× speed, so you can watch alerts fire any time. |
| `yahoo` | none | Free NSE candles and India VIX. Can lag by about a minute. |
| `angelone` | SmartAPI key, client code, MPIN, TOTP secret | Real-time candles from your Angel One account. Create an app at https://smartapi.angelone.in. Use the base32 TOTP secret, not the 6-digit code. |

The option chain comes from NSE's public API (`OPTION_CHAIN_PROVIDER=auto`). NSE sometimes blocks automated requests. When it does, the app keeps working without OI/PCR, and you can type the premium into the position calculator.

### Phone alerts (optional)

1. Create a Telegram bot with @BotFather.
2. Put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

BUY and EXIT alerts are then pushed to your phone.

## Project layout

```
app/
  indicators.py   VWAP, EMA, RSI, ATR, Supertrend, CPR/pivots, 15-min resample
  strategy.py     6-condition checklist, gates, no-trade filter, trade plan and sizing
  engine.py       scanner loop, alert de-duplication, open-trade exit monitor
  providers/      demo, yahoo, angelone (candles); options.py (NSE / demo option chain)
  fundamentals.py yfinance snapshot and checklist
  journal.py      SQLite paper-trade journal (data/journal.db)
  main.py         FastAPI REST + WebSocket server
static/           single-page dashboard (TradingView Lightweight Charts, vendored)
tests/            indicator, strategy and API tests
```

Run the tests with `.venv/bin/python -m pytest`.

## Recommended way to use it

Follow the document's last section:
1. Paper-trade the first 20-30 signals using the journal.
2. Review the win rate and average win/loss.
3. Only then trade real capital, and start with 1 lot.

---
For educational purposes only, not investment advice. About 9 in 10 individual F&O traders lose money (SEBI). Consult a SEBI-registered advisor before trading.
