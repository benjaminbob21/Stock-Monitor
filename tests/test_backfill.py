"""Historical-news backfill + PIT sentiment lookup tests (all network-free)."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_monitor.backfill import (
    aggregate_daily_sentiment,
    backfill_news,
    make_sentiment_lookup,
)
from stock_monitor.config import Settings
from stock_monitor.sentiment import NewsItem, SentimentAnalyzer
from stock_monitor.storage.db import Storage


class _KeywordAnalyzer(SentimentAnalyzer):
    """Deterministic analyzer: +1 for 'beats', -1 for 'misses', else 0."""

    name = "keyword"

    def score(self, text: str) -> float:
        low = text.lower()
        if "beats" in low:
            return 1.0
        if "misses" in low:
            return -1.0
        return 0.0


def _item(headline: str, when: dt.datetime | None) -> NewsItem:
    return NewsItem(headline=headline, url="", source="test", published=when)


def test_aggregate_collapses_to_daily_mean() -> None:
    day = dt.datetime(2024, 3, 1, 10)
    items = [
        _item("Acme beats estimates", day),
        _item("Acme misses on revenue", day.replace(hour=14)),
        _item("Acme flat guidance", dt.datetime(2024, 3, 2, 9)),
    ]
    frame = aggregate_daily_sentiment("ACME", items, _KeywordAnalyzer())

    assert list(frame["date"]) == [dt.date(2024, 3, 1), dt.date(2024, 3, 2)]
    # Day 1: mean(+1, -1) = 0 across 2 articles; Day 2: 0 across 1.
    row1 = frame[frame["date"] == dt.date(2024, 3, 1)].iloc[0]
    assert row1["sentiment"] == 0.0
    assert row1["article_count"] == 2
    assert frame["backend"].unique().tolist() == ["keyword"]


def test_aggregate_skips_items_without_a_date() -> None:
    items = [_item("Acme beats", None), _item("Acme beats", dt.datetime(2024, 1, 5))]
    frame = aggregate_daily_sentiment("ACME", items, _KeywordAnalyzer())
    assert len(frame) == 1
    assert frame.iloc[0]["sentiment"] == 1.0


def test_aggregate_caps_articles_per_day() -> None:
    day = dt.datetime(2024, 2, 2, 12)
    items = [_item("Acme beats", day) for _ in range(10)]
    frame = aggregate_daily_sentiment("ACME", items, _KeywordAnalyzer(), max_per_day=3)
    assert frame.iloc[0]["article_count"] == 3


def test_sentiment_lookup_is_point_in_time() -> None:
    daily = pd.DataFrame(
        {
            "date": [dt.date(2024, 1, 1), dt.date(2024, 1, 20), dt.date(2024, 2, 15)],
            "sentiment": [1.0, -1.0, 0.5],
        }
    )
    lookup = make_sentiment_lookup(daily, window_days=30)

    # As of Jan 25: only Jan 1 (outside 30d) excluded? Jan 1 is 24d before -> included.
    # Window [Dec 26..Jan 25] -> Jan 1 (1.0) and Jan 20 (-1.0) -> mean 0.0.
    assert lookup(dt.date(2024, 1, 25)) == 0.0
    # As of Jan 15: only Jan 1 is knowable (Jan 20 is in the future) -> 1.0.
    assert lookup(dt.date(2024, 1, 15)) == 1.0
    # No news yet before this date -> neutral 0.0.
    assert lookup(dt.date(2023, 12, 1)) == 0.0


def test_empty_lookup_returns_neutral() -> None:
    lookup = make_sentiment_lookup(pd.DataFrame(columns=["date", "sentiment"]))
    assert lookup(dt.date(2024, 1, 1)) == 0.0


class _FakeRangeProvider:
    name = "fake-range"

    def __init__(self, items: list[NewsItem]) -> None:
        self._items = items

    def get_news_range(self, ticker, from_date, to_date, *, limit=1000):
        return self._items


def test_backfill_round_trips_through_storage() -> None:
    items = [
        _item("Acme beats estimates", dt.datetime(2024, 3, 1, 10)),
        _item("Acme misses badly", dt.datetime(2024, 3, 2, 10)),
    ]
    settings = Settings(news_backfill_years=5, news_backfill_max_per_day=50)
    provider = _FakeRangeProvider(items)

    with Storage(":memory:") as store:
        written = backfill_news(
            settings,
            provider,
            store,
            ["ACME"],
            analyzer=_KeywordAnalyzer(),
            today=dt.date(2024, 3, 10),
        )
        assert written == 2
        stored = store.read_news_sentiment("ACME")
        assert len(stored) == 2
        assert store.count("news_sentiment") == 2

        # Re-running is idempotent (upsert on (ticker, date)).
        backfill_news(
            settings, provider, store, ["ACME"],
            analyzer=_KeywordAnalyzer(), today=dt.date(2024, 3, 10),
        )
        assert store.count("news_sentiment") == 2
