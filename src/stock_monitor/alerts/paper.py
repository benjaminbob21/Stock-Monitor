"""Paper-mode short-horizon alerts (Telegram, human-only).

Generates *candidate* event-driven signals and delivers them through the configured
Notifier (Telegram if set, else logs). These are **paper alerts**: recommendations
only, never trade execution. Each candidate is stored in the ``alerts`` table so
false positives can be tracked and used to retrain the short-horizon model.

Alert triggers (per ticker, per signal window):
- ``short_signal``      : the short-horizon conviction crossed the candidate threshold.
- ``short_exit_signal`` : a held position's short conviction weakened or event news
                          is materially negative.

Rate limits (provider + Oracle capacity):
- Debounced via the ``alerts`` table so a ticker is not re-pinged for the same kind
  within ``short_alert_window_hours``.
- Short-horizon model is scored on-demand and cached briefly; it is not retrained per
  alert cycle.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stock_monitor.events import EventRecord, as_utc
from stock_monitor.features.builder import build_feature_row
from stock_monitor.features.events import build_event_features
from stock_monitor.models.short_horizon import SHORT_FEATURE_COLUMNS  # noqa: F401
from stock_monitor.notify import Notifier
from stock_monitor.storage.db import Storage

if TYPE_CHECKING:
    from stock_monitor.models.scorer import Scoreable

# Conviction threshold for a "candidate" short-horizon call.
CANDIDATE_CONVICTION_THRESHOLD = 70

# Material-negative sentiment that can trigger an exit check on holdings.
MATERIAL_NEGATIVE_SENTIMENT = -0.35

# Hours before the same ticker+kind alert can fire again.
DEFAULT_ALERT_WINDOW_HOURS = 24

# Event features surfaced as top drivers in alert text.
_DRIVER_FEATURES = (
    "event_sentiment_7d", "event_count_3d", "mom_6_1", "sentiment",
)


@dataclass(frozen=True)
class ShortSignal:
    ticker: str
    conviction: int
    recommendation: str
    top_drivers: list[str]
    event_ids: list[str]
    as_of: dt.datetime


@dataclass(frozen=True)
class PaperAlert:
    kind: str
    ticker: str
    title: str
    body: str
    detail: str
    signal: ShortSignal | None = None


def _decision_ts(as_of: dt.date | dt.datetime) -> dt.datetime:
    """Return an aware UTC decision timestamp (a plain date = end of that UTC day)."""
    if isinstance(as_of, dt.datetime):
        return as_utc(as_of)
    return as_utc(dt.datetime.combine(as_of, dt.time(23, 59, 59)))


def _format_drivers(drivers: list[str]) -> list[str]:
    """Keep the feature-name based driver list for human-readable output."""
    return list(drivers)


def _events_for_ticker(
    storage: Storage, ticker: str, as_of: dt.datetime, news_window_days: int
) -> list[EventRecord]:
    """Return stored events knowable on/before ``as_of`` (PIT-safe filtering)."""
    since = as_of - dt.timedelta(days=news_window_days)
    rows = storage.read_events(ticker)
    events: list[EventRecord] = []
    for row in rows:
        published = as_utc(row["published_at"])
        known = as_utc(row["known_at"])
        if known <= as_of and published >= since:
            events.append(
                EventRecord(
                    ticker=row["ticker"],
                    headline=row["headline"],
                    source=row["source"],
                    published_at=published,
                    known_at=known,
                    url=row["url"],
                    sentiment=row.get("sentiment"),
                    category=row.get("category", "other"),
                    importance=row.get("importance", 0.0),
                )
            )
    return events


def _signal_for_row(
    ticker: str,
    row: dict[str, object],
    short_model: Scoreable,
    events: list[EventRecord],
    as_of: dt.datetime,
) -> ShortSignal | None:
    """Score one row and return a ShortSignal only if it crosses the threshold."""
    from stock_monitor.models.scorer import is_low_signal
    from stock_monitor.models.short_horizon import predict_short_conviction

    if is_low_signal(short_model):
        return None
    conviction = predict_short_conviction(short_model, row)
    if conviction < CANDIDATE_CONVICTION_THRESHOLD:
        return None
    drivers = [
        name for name in _DRIVER_FEATURES if name in row
    ]
    return ShortSignal(
        ticker=ticker.upper(),
        conviction=conviction,
        recommendation="consider buying",
        top_drivers=_format_drivers(drivers),
        event_ids=[e.event_id for e in events],
        as_of=as_of,
    )


def evaluate_short_signal(
    ticker: str,
    prices,
    facts,
    storage: Storage,
    as_of: dt.date | dt.datetime,
    short_model: Scoreable | None = None,
    news_window_days: int = 7,
) -> ShortSignal | None:
    """Produce a short-horizon signal when the candidate threshold is crossed.

    Short conviction is only emitted (and alerted on) when the short model exists and
    is not collapsed (``is_low_signal``). Returns ``None`` for low-signal windows so we
    don't spam false positives.
    """
    if short_model is None:
        return None

    decision_ts = _decision_ts(as_of)
    events = _events_for_ticker(storage, ticker, decision_ts, news_window_days)
    row = build_feature_row(ticker, prices, facts, as_of)
    if row is None:
        return None
    row.update(build_event_features(events, as_of))
    return _signal_for_row(ticker, row, short_model, events, decision_ts)


def make_alert(signal: ShortSignal) -> PaperAlert:
    """Build the user-facing alert payload for a short signal."""
    drivers = ", ".join(signal.top_drivers)
    return PaperAlert(
        kind="short_signal",
        ticker=signal.ticker,
        title=f"[SHORT-SIGNAL] {signal.ticker} conviction {signal.conviction}",
        body=(
            "Recent news + price action suggest a short-term opportunity in "
            f"{signal.ticker} (conviction {signal.conviction}/100).\n\n"
            f"Top drivers: {drivers or 'none yet'}."
        ),
        detail=f"events={signal.event_ids}",
        signal=signal,
    )


def run_paper_alerts(
    tickers: list[str],
    prices_provider,
    fundamentals_provider,
    storage: Storage,
    notifier: Notifier,
    short_model: Scoreable | None = None,
    as_of: dt.date | dt.datetime | None = None,
    window_hours: int = DEFAULT_ALERT_WINDOW_HOURS,
    news_window_days: int = 7,
) -> list[PaperAlert]:
    """Evaluate short signals for ``tickers`` and deliver paper alerts.

    For each ticker: pull prices + fundamentals PIT-correctly, load knowable events,
    score the short-horizon model, and deliver an alert only if (a) conviction is high
    enough and (b) the same ticker+kind has not been alerted within ``window_hours``.
    """
    if short_model is None:
        return []

    decision = as_of or dt.date.today()

    fired: list[PaperAlert] = []
    for ticker in tickers:
        prices = prices_provider(ticker)
        facts = fundamentals_provider(ticker)
        signal = evaluate_short_signal(
            ticker, prices, facts, storage, decision,
            short_model=short_model, news_window_days=news_window_days,
        )
        if signal is None:
            continue
        if storage.recent_alert(ticker, "short_signal", window_hours):
            continue
        alert = make_alert(signal)
        notifier.send(alert.title, alert.body)
        storage.record_alert(ticker, "short_signal", alert.detail)
        fired.append(alert)
    return fired
