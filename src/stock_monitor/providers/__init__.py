"""Data providers (prices, fundamentals) behind a swappable interface."""

from stock_monitor.providers.base import (
    PRICE_COLUMNS,
    FundamentalFact,
    FundamentalProvider,
    PriceProvider,
)
from stock_monitor.providers.edgar_provider import EdgarProvider
from stock_monitor.providers.yfinance_provider import YFinanceProvider


def get_price_provider(settings: object | None = None) -> PriceProvider:
    """Return the configured price provider, defaulting to yfinance.

    Selection is driven by ``settings.price_provider`` ("yfinance" | "tiingo" | "eodhd").
    A keyed source silently falls back to yfinance if its key is missing, so serving
    never breaks just because a paid key isn't set.
    """
    if settings is None:
        return YFinanceProvider()

    choice = str(getattr(settings, "price_provider", "yfinance")).lower()
    if choice == "tiingo" and getattr(settings, "tiingo_api_key", ""):
        from stock_monitor.providers.tiingo_provider import TiingoProvider

        return TiingoProvider(settings.tiingo_api_key)
    if choice == "eodhd" and getattr(settings, "eodhd_api_key", ""):
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
