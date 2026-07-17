"""Alpha Vantage NEWS_SENTIMENT provider — historical news for the gap backfill.

Finnhub's free company-news only reaches back ~1 year and FNSPID stops at 2024-01, so
the window ``2024-01 -> ~1yr-ago`` has no news. Alpha Vantage's ``NEWS_SENTIMENT``
endpoint accepts an explicit ``time_from``/``time_to`` range (back to ~2022) and returns
up to 1000 articles per call — enough to close that gap on the **free 25-requests/day**
tier via a throttled, resumable backfill.

Consistency rule: we pull only the RAW ``title`` for each article and score it with our
own analyzer (FinBERT) — never Alpha Vantage's built-in sentiment — so the ``sentiment``
feature stays on one scale across all history (FNSPID + Finnhub + Alpha Vantage).
"""

from __future__ import annotations

import datetime as dt

from tenacity import retry, stop_after_attempt, wait_exponential

from stock_monitor.sentiment import NewsItem, NewsProvider

_URL = "https://www.alphavantage.co/query"


class AlphaVantageRateLimited(RuntimeError):
    """Raised when Alpha Vantage returns a quota/throttle notice instead of a feed.

    The free tier caps at 25 requests/day; when exhausted the API returns a JSON body
    with an ``Information`` note and no ``feed``. The backfill catches this to stop
    cleanly for the day and resume tomorrow.
    """


class AlphaVantageNewsProvider(NewsProvider):
    """Company news from Alpha Vantage's ``NEWS_SENTIMENT`` endpoint (range-capable)."""

    name = "alphavantage-news"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("AlphaVantageNewsProvider requires an API key")
        self._api_key = api_key

    def get_news(self, ticker: str, lookback_days: int) -> list[NewsItem]:
        today = dt.date.today()
        return self.get_news_range(ticker, today - dt.timedelta(days=lookback_days), today)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        reraise=True,
    )
    def get_news_range(
        self, ticker: str, from_date: dt.date, to_date: dt.date, *, limit: int = 1000
    ) -> list[NewsItem]:
        """Return news items published in ``[from_date, to_date]`` (historical backfill).

        Sorted EARLIEST-first so a capped 1000-item response can be resumed by advancing
        ``from_date`` past the last returned article. Raises
        :class:`AlphaVantageRateLimited` when the daily quota is exhausted.
        """
        import requests

        params: dict[str, str | int] = {
            "function": "NEWS_SENTIMENT",
            "tickers": ticker.upper(),
            "time_from": f"{from_date:%Y%m%d}T0000",
            "time_to": f"{to_date:%Y%m%d}T2359",
            "sort": "EARLIEST",
            "limit": max(1, min(int(limit), 1000)),
            "apikey": self._api_key,
        }
        resp = requests.get(_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return []

        # Quota/throttle/error come back as a dict without "feed".
        if "feed" not in data:
            note = str(
                data.get("Information") or data.get("Note") or data.get("Error Message") or ""
            )
            low = note.lower()
            if any(k in low for k in ("limit", "premium", "subscribe", "thank you")):
                raise AlphaVantageRateLimited(note or "alphavantage quota exhausted")
            return []

        items: list[NewsItem] = []
        for entry in data.get("feed") or []:
            headline = entry.get("title")
            if not headline:
                continue
            published = _parse_av_time(entry.get("time_published"))
            if published is None:
                continue
            items.append(
                NewsItem(
                    headline=headline,
                    url=entry.get("url", ""),
                    source=entry.get("source", "alphavantage"),
                    published=published,
                )
            )
        return items


def _parse_av_time(value: object) -> dt.datetime | None:
    """Parse Alpha Vantage's ``YYYYMMDDTHHMMSS`` (or ``...THHMM``) timestamp."""
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
