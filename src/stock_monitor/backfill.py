"""Historical news backfill: turn past headlines into a trainable PIT sentiment feature.

The live sentiment overlay can't be learned from, because we never had *historical*
news to bake into the model's ``sentiment`` feature (it trains as 0.0). This job closes
that gap: pull years of past headlines for each ticker (via a range-capable news
provider such as EODHD), score them with the same analyzer used live (FinBERT/VADER),
aggregate to a daily mean, and store it in ``news_sentiment``. The feature builder can
then join that daily series in PIT-correctly — so the model finally learns from how
news moved similar names in the past.

Design notes:
- One-time cost: run once against a paid month of deep history, then train forever on
  the stored snapshot. Free-tier keys still work for shallow (≈1-year) backfills.
- ``aggregate_daily_sentiment`` is pure (no network) so the logic is unit-testable.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Protocol

import pandas as pd

from stock_monitor.config import Settings
from stock_monitor.sentiment import NewsItem, SentimentAnalyzer, get_sentiment_analyzer
from stock_monitor.storage.db import Storage


class RangeNewsProvider(Protocol):
    """A news provider that can return items for an explicit date range."""

    name: str

    def get_news_range(
        self, ticker: str, from_date: dt.date, to_date: dt.date, *, limit: int = ...
    ) -> list[NewsItem]: ...


def aggregate_daily_sentiment(
    ticker: str,
    items: list[NewsItem],
    analyzer: SentimentAnalyzer,
    *,
    max_per_day: int = 50,
) -> pd.DataFrame:
    """Score headlines and collapse them to one sentiment value per calendar day.

    Returns a DataFrame with columns ``ticker``, ``date``, ``sentiment``,
    ``article_count``, ``backend``. Items without a publish date are ignored (a PIT
    feature needs a known date). At most ``max_per_day`` items are scored per day to
    keep FinBERT cost bounded.
    """
    empty = pd.DataFrame(
        columns=["ticker", "date", "sentiment", "article_count", "backend"]
    )
    if not items:
        return empty

    by_day: dict[dt.date, list[str]] = {}
    for item in items:
        if item.published is None or not item.headline:
            continue
        day = item.published.date()
        bucket = by_day.setdefault(day, [])
        if len(bucket) < max_per_day:
            bucket.append(item.headline)

    rows: list[dict[str, object]] = []
    for day, headlines in by_day.items():
        if not headlines:
            continue
        scores = [analyzer.score(h) for h in headlines]
        rows.append(
            {
                "ticker": ticker.upper(),
                "date": day,
                "sentiment": float(sum(scores) / len(scores)),
                "article_count": len(headlines),
                "backend": analyzer.name,
            }
        )

    if not rows:
        return empty
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def make_sentiment_lookup(
    daily: pd.DataFrame,
    *,
    window_days: int = 30,
) -> Callable[[dt.date], float]:
    """Build a PIT sentiment lookup: mean daily sentiment over the trailing window.

    ``daily`` is a per-ticker frame with ``date`` and ``sentiment`` columns (as stored
    by the backfill). The returned callable, given an ``as_of`` date, averages only the
    sentiment knowable on or before that date — never peeking into the future — so it is
    safe to feed into :func:`build_feature_row` / :func:`build_training_frame`.
    """
    if daily is None or daily.empty:
        return lambda _as_of: 0.0

    series = (
        daily[["date", "sentiment"]]
        .assign(date=lambda d: pd.to_datetime(d["date"]).dt.date)
        .dropna(subset=["sentiment"])
        .sort_values("date")
    )
    dates = series["date"].to_list()
    values = series["sentiment"].astype(float).to_list()

    def lookup(as_of: dt.date) -> float:
        lo = as_of - dt.timedelta(days=window_days)
        window = [v for d, v in zip(dates, values) if lo <= d <= as_of]
        return float(sum(window) / len(window)) if window else 0.0

    return lookup


def backfill_news(
    settings: Settings,
    provider: RangeNewsProvider,
    storage: Storage,
    tickers: list[str],
    *,
    analyzer: SentimentAnalyzer | None = None,
    today: dt.date | None = None,
) -> int:
    """Backfill and store daily news sentiment for ``tickers``. Returns rows written.

    Pulls ``settings.news_backfill_years`` of history per ticker, scores it, and upserts
    the daily series. Per-ticker failures are swallowed so one bad symbol can't abort the
    whole run.
    """
    analyzer = analyzer or get_sentiment_analyzer(settings)
    end = today or dt.date.today()
    start = end - dt.timedelta(days=365 * settings.news_backfill_years)
    written = 0

    for ticker in tickers:
        try:
            items = provider.get_news_range(ticker, start, end, limit=1000)
        except Exception:  # noqa: BLE001 — one symbol must not abort the backfill
            continue
        frame = aggregate_daily_sentiment(
            ticker, items, analyzer, max_per_day=settings.news_backfill_max_per_day
        )
        if not frame.empty:
            written += storage.upsert_news_sentiment(frame)
    return written
