"""Optional Telegram push so alerts reach your phone."""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)


def telegram(text: str) -> None:
    if not (settings.telegram_token and settings.telegram_chat_id):
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage",
                   json={"chat_id": settings.telegram_chat_id, "text": text}, timeout=10)
    except httpx.HTTPError as e:
        log.warning("Telegram alert failed: %s", e)
