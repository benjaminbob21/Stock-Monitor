"""EODHD provider: deep-history prices + *historical* news (the learn-from-history unlock).

Two roles live here because they share one API key:

- :class:`EODHDProvider` — split/dividend-adjusted EOD prices (:class:`PriceProvider`).
- :class:`EODHDNewsProvider` — company news that, unlike the live-only yfinance feed,
  can be pulled for an arbitrary **date range**. That range fetch is what makes a
  one-time historical backfill (and therefore a trainable ``sentiment`` feature) possible.

Both are key-gated: constructed only when ``eodhd_api_key`` is set. EODHD US symbols
take the ``AAPL.US`` form; a bare ``AAPL`` is normalised automatically.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from stock_monitor.providers.base import PRICE_COLUMNS, PriceProvider
from stock_monitor.sentiment import NewsItem, NewsProvider

_EOD_URL = "https://eodhd.com/api/eod"
_NEWS_URL = "https://eodhd.com/api/news"


def _eodhd_symbol(ticker: str) -> str:
    """Normalise a bare ticker to EODHD's ``SYMBOL.EXCHANGE`` form (defaulting to US)."""
    t = ticker.upper()
    return t if "." in t else f"{t}.US"


class EODHDProvider(PriceProvider):
    """Fetch split/dividend-adjusted OHLCV history from EODHD."""

    name = "eodhd"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("EODHDProvider requires an API key")
        self._api_key = api_key

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        reraise=True,
    )
    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        import requests

        resp = requests.get(
            f"{_EOD_URL}/{_eodhd_symbol(ticker)}",
            params={
                "api_token": self._api_key,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "period": "d",
                "fmt": "json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return pd.DataFrame(columns=list(PRICE_COLUMNS))

        df = pd.DataFrame(rows)
        # EODHD gives raw OHLC + a single adjusted_close. Scale OHLC by the adjustment
        # factor so every column is on the same split/dividend-adjusted basis.
        if "adjusted_close" in df.columns and "close" in df.columns:
            factor = (df["adjusted_close"] / df["close"]).replace([float("inf")], 1.0).fillna(1.0)
            for col in ("open", "high", "low"):
                if col in df.columns:
                    df[col] = df[col] * factor
            df["close"] = df["adjusted_close"]
            if "volume" in df.columns:
                df["volume"] = df["volume"] / factor.replace(0.0, 1.0)

        df = df[["date", *PRICE_COLUMNS]].copy()
        df.index = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df.index.name = "date"
        df = df.drop(columns=["date"])
        return df.sort_index()


class EODHDNewsProvider(NewsProvider):
    """Company news from EODHD, with a live fetch *and* a historical range fetch."""

    name = "eodhd-news"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("EODHDNewsProvider requires an API key")
        self._api_key = api_key

    def get_news(self, ticker: str, lookback_days: int) -> list[NewsItem]:
        today = dt.date.today()
        return self.get_news_range(
            ticker, today - dt.timedelta(days=lookback_days), today, limit=100
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        reraise=True,
    )
    def get_news_range(
        self, ticker: str, from_date: dt.date, to_date: dt.date, *, limit: int = 1000
    ) -> list[NewsItem]:
        """Return news items published in ``[from_date, to_date]`` (historical backfill)."""
        import requests

        resp = requests.get(
            _NEWS_URL,
            params={
                "api_token": self._api_key,
                "s": _eodhd_symbol(ticker),
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "limit": limit,
                "fmt": "json",
            },
            timeout=20,
        )
        resp.raise_for_status()
        raw = resp.json()
        if not isinstance(raw, list):
            return []

        items: list[NewsItem] = []
        for entry in raw:
            headline = entry.get("title")
            if not headline:
                continue
            published = _parse_eodhd_date(entry.get("date"))
            items.append(
                NewsItem(
                    headline=headline,
                    url=entry.get("link", ""),
                    source="eodhd",
                    published=published,
                )
            )
        return items


def _parse_eodhd_date(value: object) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None
