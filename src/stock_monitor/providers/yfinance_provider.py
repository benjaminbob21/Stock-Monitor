"""yfinance price provider.

Great for prototyping and years of daily history at $0. Unofficial and can throttle,
so it lives behind :class:`PriceProvider` and must never be the *only* source in
production (see build-plan §8 risk #3).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from stock_monitor.providers.base import PRICE_COLUMNS, PriceProvider


class YFinanceProvider(PriceProvider):
    """Fetch split/dividend-adjusted OHLCV history from Yahoo Finance."""

    name = "yfinance"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        reraise=True,
    )
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

    def get_quote(self, ticker: str) -> float | None:
        """Latest intraday price via yfinance ``fast_info`` (no retry, best-effort).

        Yahoo's ``fast_info`` returns a live last price during market hours and the
        most recent close otherwise. It is fast and unthrottled relative to the
        history endpoint, but can still fail transiently — any error yields ``None``
        so the caller falls back to the last completed daily close.
        """
        import yfinance as yf

        try:
            fast = yf.Ticker(ticker).fast_info
            price = float(fast.last_price)
        except Exception:  # noqa: BLE001 — live quote is best-effort; degrade to close
            return None
        if price != price or price <= 0:  # NaN or non-positive
            return None
        return price
