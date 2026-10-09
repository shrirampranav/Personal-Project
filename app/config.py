"""Runtime settings, loaded from environment variables / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _list(name: str, default: str) -> list[str]:
    return [s.strip().upper() for s in _env(name, default).split(",") if s.strip()]


@dataclass
class Settings:
    # Data source for candles: "demo", "yahoo" or "angelone"
    provider: str = field(default_factory=lambda: _env("DATA_PROVIDER", "demo").lower())
    # Option chain source: "nse", "demo" or "off"
    option_chain: str = field(default_factory=lambda: _env("OPTION_CHAIN_PROVIDER", "auto").lower())
    watchlist: list[str] = field(default_factory=lambda: _list(
        "WATCHLIST", "NIFTY,BANKNIFTY,RELIANCE,HDFCBANK,ICICIBANK,INFY,TCS,SBIN"))
    refresh_seconds: float = field(default_factory=lambda: _float("REFRESH_SECONDS", 30))

    # Risk rules (from the setup document)
    capital: float = field(default_factory=lambda: _float("CAPITAL", 200000))
    risk_per_trade_pct: float = field(default_factory=lambda: _float("RISK_PER_TRADE_PCT", 1.0))
    max_losing_trades_per_day: int = field(default_factory=lambda: int(_float("MAX_LOSING_TRADES", 2)))
    daily_loss_limit: float = field(default_factory=lambda: _float("DAILY_LOSS_LIMIT", 0))  # 0 = off
    premium_stop_pct: float = field(default_factory=lambda: _float("PREMIUM_STOP_PCT", 30))

    # Strategy thresholds
    rsi_bull: float = 60
    rsi_bear: float = 40
    vix_high: float = 18
    vix_low: float = 12
    volume_mult: float = field(default_factory=lambda: _float("VOLUME_MULT", 1.5))
    narrow_cpr_pct: float = 0.25

    # Angel One SmartAPI
    angel_api_key: str = field(default_factory=lambda: _env("ANGEL_API_KEY"))
    angel_client_code: str = field(default_factory=lambda: _env("ANGEL_CLIENT_CODE"))
    angel_pin: str = field(default_factory=lambda: _env("ANGEL_PIN"))
    angel_totp_secret: str = field(default_factory=lambda: _env("ANGEL_TOTP_SECRET"))

    # Optional Telegram push alerts
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", str(ROOT / "data" / "journal.db"))))


settings = Settings()
