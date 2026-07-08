"""Data providers (prices, fundamentals) behind a swappable interface."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stock_monitor.providers.base import (
    PRICE_COLUMNS,
    FundamentalFact,
    FundamentalProvider,
    PriceProvider,
)
from stock_monitor.providers.edgar_provider import EdgarProvider
from stock_monitor.providers.yfinance_provider import YFinanceProvider

if TYPE_CHECKING:
    from stock_monitor.config import Settings


def get_price_provider(settings: Settings | None = None) -> PriceProvider:
    """Return the configured price provider, defaulting to yfinance.

    Selection is driven by ``settings.price_provider`` ("yfinance" | "tiingo" | "eodhd").
    A keyed source silently falls back to yfinance if its key is missing, so serving
    never breaks just because a paid key isn't set.
    """
    if settings is None:
        return YFinanceProvider()

    choice = settings.price_provider.lower()
    if choice == "tiingo" and settings.tiingo_api_key:
        from stock_monitor.providers.tiingo_provider import TiingoProvider

        return TiingoProvider(settings.tiingo_api_key)
    if choice == "eodhd" and settings.eodhd_api_key:
        from stock_monitor.providers.eodhd_provider import EODHDProvider

        return EODHDProvider(settings.eodhd_api_key)
    return YFinanceProvider()


__all__ = [
    "PRICE_COLUMNS",
    "FundamentalFact",
    "FundamentalProvider",
    "PriceProvider",
    "EdgarProvider",
    "YFinanceProvider",
    "get_price_provider",
]
