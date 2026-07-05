"""Tiingo price provider.

Tiingo's EOD price feed is reliable and its free tier covers deep daily history for
US names — a sturdier alternative to the unofficial yfinance feed for the *live*
serving path. Key-gated: only used when ``tiingo_api_key`` is set.

Returns the same contract as every :class:`PriceProvider`: a tz-naive DatetimeIndex
named ``date`` with split/dividend-adjusted OHLCV columns.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from stock_monitor.providers.base import PRICE_COLUMNS, PriceProvider

_BASE = "https://api.tiingo.com/tiingo/daily"


class TiingoProvider(PriceProvider):
    """Fetch split/dividend-adjusted OHLCV history from Tiingo."""

    name = "tiingo"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("TiingoProvider requires an API key")
        self._api_key = api_key

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        reraise=True,
    )
    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        import requests

        url = f"{_BASE}/{ticker.lower()}/prices"
        resp = requests.get(
            url,
            params={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "format": "json",
                "resampleFreq": "daily",
            },
            headers={
                "Authorization": f"Token {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return pd.DataFrame(columns=list(PRICE_COLUMNS))

        df = pd.DataFrame(rows)
        # Prefer the split/dividend-adjusted columns so returns are comparable over time.
        rename = {
            "adjOpen": "open",
            "adjHigh": "high",
            "adjLow": "low",
            "adjClose": "close",
            "adjVolume": "volume",
        }
        missing = [c for c in rename if c not in df.columns]
        if missing:  # fall back to raw columns if adjusted ones are absent
            rename = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}

        df = df.rename(columns=rename)[["date", *PRICE_COLUMNS]].copy()
        df.index = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df.index.name = "date"
        df = df.drop(columns=["date"])
        return df.sort_index()
