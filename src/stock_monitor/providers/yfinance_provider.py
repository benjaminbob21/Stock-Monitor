"""yfinance price provider.

Great for prototyping and years of daily history at $0. Unofficial and can throttle,
so it lives behind :class:`PriceProvider` and must never be the *only* source in
production (see build-plan §8 risk #3).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_monitor.providers.base import PRICE_COLUMNS, PriceProvider


class YFinanceProvider(PriceProvider):
    """Fetch split/dividend-adjusted OHLCV history from Yahoo Finance."""

    name = "yfinance"

    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        import yfinance as yf

        raw = yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            actions=False,
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=list(PRICE_COLUMNS))

        # Flatten any MultiIndex columns yfinance may return for a single ticker.
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        rename = {
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
        df = raw.rename(columns=rename)[list(PRICE_COLUMNS)].copy()

        # Normalise to a tz-naive DatetimeIndex named "date" for clean joins.
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "date"
        return df.sort_index()
