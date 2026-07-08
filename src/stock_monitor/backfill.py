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

from stock_monitor.config import Settings, get_settings
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

    # Flatten and score every headline in one batched pass (big FinBERT speedup),
    # then fold the scores back per day.
    flat_headlines: list[str] = []
    day_spans: list[tuple[dt.date, int, int]] = []
    for day, headlines in by_day.items():
        if not headlines:
            continue
        start = len(flat_headlines)
        flat_headlines.extend(headlines)
        day_spans.append((day, start, len(flat_headlines)))

    if not flat_headlines:
        return empty

    all_scores = analyzer.score_batch(flat_headlines)

    rows: list[dict[str, object]] = []
    for day, start, end in day_spans:
        scores = all_scores[start:end]
        rows.append(
            {
                "ticker": ticker.upper(),
                "date": day,
                "sentiment": float(sum(scores) / len(scores)),
                "article_count": end - start,
                "backend": analyzer.name,
            }
        )

    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def articles_frame(
    ticker: str,
    items: list[NewsItem],
    analyzer: SentimentAnalyzer,
) -> pd.DataFrame:
    """Build a raw-headline archive frame from scored news items.

    Returns columns ``ticker``, ``published``, ``headline``, ``source``, ``url``,
    ``sentiment``, ``backend`` — the durable record we keep forever so news can be
    re-scored later without re-buying it. Undated or headline-less items are dropped
    (the publish timestamp anchors the permanent key).
    """
    empty = pd.DataFrame(
        columns=["ticker", "published", "headline", "source", "url", "sentiment", "backend"]
    )
    rows: list[dict[str, object]] = []
    for item in items:
        if item.published is None or not item.headline:
            continue
        rows.append(
            {
                "ticker": ticker.upper(),
                "published": pd.Timestamp(item.published),
                "headline": item.headline,
                "source": item.source or "",
                "url": item.url or "",
                "sentiment": float(analyzer.score(item.headline)),
                "backend": analyzer.name,
            }
        )
    if not rows:
        return empty
    return (
        pd.DataFrame(rows)
        .drop_duplicates(subset=["ticker", "published", "headline"])
        .sort_values("published")
        .reset_index(drop=True)
    )


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
        window = [v for d, v in zip(dates, values, strict=True) if lo <= d <= as_of]
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
        archive = articles_frame(ticker, items, analyzer)
        if not archive.empty:
            storage.upsert_news_articles(archive)
    return written


def main(argv: list[str] | None = None) -> int:
    """CLI: one-time historical news backfill into the ``news_sentiment`` table.

    Run with ``stock-monitor-backfill``. Uses the EODHD news API (needs ``EODHD_API_KEY``);
    on the free tier you get roughly the past year, on a paid month the full
    ``NEWS_BACKFILL_YEARS`` window. The scored daily sentiment is stored so training can
    finally learn from news history — this is a snapshot you keep forever.
    """
    import argparse

    from stock_monitor.providers.eodhd_provider import EODHDNewsProvider
    from stock_monitor.universe import get_universe

    parser = argparse.ArgumentParser(description="Stock-Monitor historical news backfill")
    parser.add_argument(
        "-w", "--watchlist", nargs="+", default=None,
        help="Tickers to backfill (default: the scan universe).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.eodhd_api_key:
        print("EODHD_API_KEY is not set — cannot backfill news.")
        return 1

    tickers = [t.upper() for t in (args.watchlist or get_universe())]
    provider = EODHDNewsProvider(settings.eodhd_api_key)
    analyzer = get_sentiment_analyzer(settings)

    print(
        f"Backfilling up to {settings.news_backfill_years}y of news for "
        f"{len(tickers)} tickers via {provider.name} (analyzer={analyzer.name}) ..."
    )
    with Storage(settings.db_path) as store:
        written = backfill_news(settings, provider, store, tickers, analyzer=analyzer)
        total = store.count("news_sentiment")
    print(
        f"Backfill complete: {written} daily rows written this run; "
        f"news_sentiment now holds {total} rows total."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
