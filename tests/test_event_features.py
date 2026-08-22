"""PIT-safe event feature tests."""

import datetime as dt

import pytest

from stock_monitor.events import EventRecord
from stock_monitor.features.events import as_decision_time, build_event_features


def _event(
    ticker: str,
    minutes_ago: int,
    *,
    sentiment: float | None = 0.0,
    importance: float = 0.5,
    category: str = "other",
) -> EventRecord:
    return EventRecord(
        ticker=ticker,
        headline=f"{category} event {minutes_ago}",
        source="test",
        published_at=dt.datetime(2025, 1, 10) - dt.timedelta(minutes=minutes_ago + 5),
        known_at=dt.datetime(2025, 1, 10) - dt.timedelta(minutes=minutes_ago),
        sentiment=sentiment,
        importance=importance,
        category=category,
    )


@pytest.mark.parametrize("tzinfo", [dt.UTC, None])
def test_decision_time_is_aware_utc(tzinfo: object) -> None:
    naive = dt.datetime(2025, 1, 10, 12, 0, tzinfo=tzinfo)  # type: ignore[arg-type]
    decision = as_decision_time(naive)
    assert decision.tzinfo is not None
    assert decision.utcoffset() == dt.timedelta(0)


def test_plain_date_interpreted_as_end_of_day() -> None:
    decision = as_decision_time(dt.date(2025, 1, 10))
    assert decision.hour == 23
    assert decision.minute == 59


def test_future_events_are_excluded() -> None:
    known = dt.datetime(2025, 1, 10, 12, 0, tzinfo=dt.UTC)
    events = [
        EventRecord(
            ticker="AAA", headline="known now", source="test",
            published_at=known, known_at=known,
        ),
        EventRecord(
            ticker="AAA", headline="known later", source="test",
            published_at=known, known_at=dt.datetime(2025, 1, 10, 12, 1, tzinfo=dt.UTC),
        ),
    ]
    features = build_event_features(events, dt.datetime(2025, 1, 10, 12, 0, tzinfo=dt.UTC))
    assert features["event_count_1d"] == 1.0


def test_counts_and_sentiment_windows() -> None:
    events = [
        _event("AAA", minutes_ago=30, sentiment=0.9, importance=0.8),
        _event("AAA", minutes_ago=2 * 24 * 60, sentiment=0.2),
    ]
    features = build_event_features(events, dt.datetime(2025, 1, 10, 12, 0))
    assert features["event_count_1d"] == 1.0
    assert features["event_count_3d"] == 2.0
    assert features["event_count_7d"] == 2.0
    assert features["event_sentiment_1d"] == pytest.approx(0.9)
    assert features["event_sentiment_3d"] == pytest.approx((0.9 + 0.2) / 2)
    assert features["event_max_importance_1d"] == pytest.approx(0.8)
    assert features["days_since_last_event"] < 1.0


def test_no_eligible_events_gives_nan_sentiment_and_zero_counts() -> None:
    features = build_event_features([], dt.datetime(2025, 1, 10, 12, 0))
    assert features["event_count_3d"] == 0.0
    assert features["event_sentiment_3d"] != features["event_sentiment_3d"]  # NaN


def test_recency_weighting_prioritizes_newer_event() -> None:
    old = _event("AAA", minutes_ago=3 * 24 * 60, sentiment=-0.8)
    recent = _event("AAA", minutes_ago=30, sentiment=0.8)
    features = build_event_features([old, recent], dt.datetime(2025, 1, 10, 12, 0))
    assert features["event_sentiment_recency_7d"] > 0.5