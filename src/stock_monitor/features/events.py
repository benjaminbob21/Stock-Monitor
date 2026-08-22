"""PIT-safe event-derived features for the short-horizon model.

Converts persisted :class:`~stock_monitor.events.EventRecord` values into rolling
features *as of* a decision timestamp. Only events whose ``known_at`` is on or before
the ``as_of`` moment are eligible, so the returned features can never peek into the
future (the same point-in-time rule the price/fundamental features follow).

Feature groups:
- event velocity : event_count_{1,3,7}d — how many events were knowable in window.
- rolling sentiment : event_sentiment_{1,3,7}d — mean stored sentiment per window.
- recency-weighted sentiment : event_sentiment_recency_7d — decay-weighted mean.
- importance : event_importance_{3,7}d + event_max_importance_1d — mean / peak.
- novelty : days_since_last_event — freshness of the most recent known event.

``NaN`` is intentional and means "no eligible events" (missing data), mirroring the
fundamental features — the model treats it as absent rather than fabricating a value.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence

import numpy as np

from stock_monitor.events import EventRecord, as_utc

EVENT_FEATURE_COLUMNS: tuple[str, ...] = (
    "event_count_1d",
    "event_count_3d",
    "event_count_7d",
    "event_sentiment_1d",
    "event_sentiment_3d",
    "event_sentiment_7d",
    "event_sentiment_recency_7d",
    "event_importance_3d",
    "event_importance_7d",
    "event_max_importance_1d",
    "days_since_last_event",
)

_WINDOW_DAYS: tuple[int, ...] = (1, 3, 7)
_RECENCY_HALF_LIFE_DAYS = 1.0


def as_decision_time(as_of: dt.date | dt.datetime) -> dt.datetime:
    """Normalize a decision timestamp to an aware UTC instant.

    Naive datetimes are treated as UTC (backend convention); a plain ``date`` is
    interpreted as the *end* of that UTC day, matching how an as-of date includes all
    news that became knowable during that day.
    """
    if isinstance(as_of, dt.datetime):
        return as_utc(as_of)
    return as_utc(dt.datetime.combine(as_of, dt.time(23, 59, 59)))


def build_event_features(
    events: Sequence[EventRecord],
    as_of: dt.date | dt.datetime,
) -> dict[str, float]:
    """Return PIT-safe rolling event features knowable on/ before ``as_of``.

    Events with ``known_at`` after the decision time are dropped entirely, so late or
    backfilled rows can never contaminate a historical decision.
    """
    decision = as_decision_time(as_of)
    eligible = [e for e in events if as_utc(e.known_at) <= decision]
    if not eligible:
        return {
            column: (0.0 if "count" in column else math.nan)
            for column in EVENT_FEATURE_COLUMNS
        }

    def windowed(days: int) -> list[EventRecord]:
        cutoff = decision - dt.timedelta(days=days)
        return [e for e in eligible if as_utc(e.known_at) >= cutoff]

    def mean_sentiment(items: list[EventRecord]) -> float:
        values = [e.sentiment for e in items if e.sentiment is not None]
        return float(np.mean(values)) if values else math.nan

    def mean_importance(items: list[EventRecord]) -> float:
        return float(np.mean([e.importance for e in items])) if items else math.nan

    last = max(eligible, key=lambda e: e.known_at)
    w1, w3, w7 = (windowed(days) for days in _WINDOW_DAYS)

    # Recency decay: weight = 0.5^(age / half-life), so a 1-day-old event is 0.5x.
    def recency_weighted_sentiment(items: list[EventRecord]) -> float:
        weighted = 0.0
        total = 0.0
        for event in items:
            if event.sentiment is None:
                continue
            age_days = (decision - as_utc(event.known_at)).total_seconds() / 86_400.0
            weight = 0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS)
            weighted += event.sentiment * weight
            total += weight
        return float(weighted / total) if total else math.nan

    return {
        "event_count_1d": float(len(w1)),
        "event_count_3d": float(len(w3)),
        "event_count_7d": float(len(w7)),
        "event_sentiment_1d": mean_sentiment(w1),
        "event_sentiment_3d": mean_sentiment(w3),
        "event_sentiment_7d": mean_sentiment(w7),
        "event_sentiment_recency_7d": recency_weighted_sentiment(w7),
        "event_importance_3d": mean_importance(w3),
        "event_importance_7d": mean_importance(w7),
        "event_max_importance_1d": (
            float(max(e.importance for e in w1)) if w1 else math.nan
        ),
        "days_since_last_event": float(
            (decision - as_utc(last.known_at)).total_seconds() / 86_400.0
        ),
    }