from __future__ import annotations

from ..config import settings
from .base import CandleProvider, ProviderError
from .demo import DemoProvider
from .options import DemoOptionChain, NseOptionChain


def make_provider(name: str | None = None) -> CandleProvider:
    name = (name or settings.provider).lower()
    if name == "yahoo":
        from .yahoo import YahooProvider
        return YahooProvider()
    if name in ("angel", "angelone"):
        from .angelone import AngelOneProvider
        return AngelOneProvider()
    return DemoProvider()


def make_option_chain(provider: CandleProvider):
    mode = settings.option_chain
    if mode == "off":
        return None
    if mode == "demo" or (mode == "auto" and provider.name == "demo"):
        return DemoOptionChain()
    return NseOptionChain()


__all__ = ["CandleProvider", "ProviderError", "make_provider", "make_option_chain"]
