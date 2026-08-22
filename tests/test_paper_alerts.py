"""Tests for paper-mode short-horizon alert evaluation and delivery."""

import datetime as dt

import pandas as pd

from stock_monitor.alerts.paper import (
    CANDIDATE_CONVICTION_THRESHOLD,
    ShortSignal,
    evaluate_short_signal,
    make_alert,
    run_paper_alerts,
)
from stock_monitor.events import EventRecord


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, body: str) -> bool:
        self.sent.append((title, body))
        return True


class _FakeStorage:
    def __init__(self, events: pd.DataFrame | None = None) -> None:
        columns = [
            "event_id", "ticker", "headline", "source", "published_at",
            "known_at", "url", "sentiment", "category", "importance",
        ]
        self._events = events if events is not None else pd.DataFrame(columns=columns)
        self.alerts: list[tuple[str, str, str]] = []

    def read_events(self, ticker: str) -> list[dict]:
        rows = self._events[self._events["ticker"] == ticker]
        return [
            {
                "event_id": r["event_id"],
                "ticker": r["ticker"],
                "headline": r["headline"],
                "source": r["source"],
                "published_at": r["published_at"],
                "known_at": r["known_at"],
                "url": r["url"],
                "sentiment": r["sentiment"],
                "category": r["category"],
                "importance": r["importance"],
            }
            for _, r in rows.iterrows()
        ]

    def recent_alert(self, ticker: str, kind: str, within_hours: int) -> bool:
        return any(a[0] == ticker and a[1] == kind for a in self.alerts)

    def record_alert(self, ticker: str, kind: str, detail: str) -> None:
        self.alerts.append((ticker, kind, detail))


class _FakeShortModel:
    """Placeholder model object; predictions come from patched helpers."""


def _patch_short(monkeypatch, conviction: int, low_signal: bool = False):
    monkeypatch.setattr(
        "stock_monitor.models.scorer.is_low_signal", lambda model: low_signal
    )
    monkeypatch.setattr(
        "stock_monitor.models.short_horizon.predict_short_conviction",
        lambda model, row: conviction,
    )


def _price_frame(bars: int = 260, end: str = "2024-12-31") -> pd.DataFrame:
    """Business-day close frame ending on ``end`` with > 253 usable bars."""
    closes = [100.0 + i * 0.1 for i in range(bars)]
    return pd.DataFrame({"close": closes}, index=pd.bdate_range(end=end, periods=bars))


def test_evaluate_returns_signal_above_threshold(monkeypatch) -> None:
    storage = _FakeStorage()
    as_of = dt.date(2025, 1, 1)
    _patch_short(monkeypatch, conviction=CANDIDATE_CONVICTION_THRESHOLD + 5)

    signal = evaluate_short_signal(
        "AAA", _price_frame(), [], storage, as_of,
        short_model=_FakeShortModel(),
    )
    assert signal is not None
    assert signal.ticker == "AAA"
    assert signal.conviction == CANDIDATE_CONVICTION_THRESHOLD + 5
    assert signal.recommendation == "consider buying"


def test_evaluate_returns_none_below_threshold(monkeypatch) -> None:
    storage = _FakeStorage()
    _patch_short(monkeypatch, conviction=CANDIDATE_CONVICTION_THRESHOLD - 10)
    signal = evaluate_short_signal(
        "AAA", _price_frame(), [], storage, dt.date(2025, 1, 1),
        short_model=_FakeShortModel(),
    )
    assert signal is None


def test_evaluate_returns_none_when_low_signal(monkeypatch) -> None:
    storage = _FakeStorage()
    _patch_short(monkeypatch, conviction=95, low_signal=True)
    signal = evaluate_short_signal(
        "AAA", _price_frame(), [], storage, dt.date(2025, 1, 1),
        short_model=_FakeShortModel(),
    )
    assert signal is None


def test_evaluate_returns_none_without_short_model() -> None:
    storage = _FakeStorage()
    signal = evaluate_short_signal(
        "AAA", _price_frame(), [], storage, dt.date(2025, 1, 1),
        short_model=None,
    )
    assert signal is None


def test_evaluate_returns_none_when_price_history_too_short() -> None:
    storage = _FakeStorage()
    signal = evaluate_short_signal(
        "AAA", _price_frame(bars=100), [], storage, dt.date(2025, 1, 1),
        short_model=_FakeShortModel(),
    )
    assert signal is None


def test_events_pit_filtered_by_known_at(monkeypatch) -> None:
    late = pd.Timestamp("2025-01-05", tz="UTC")  # known after the decision date.
    early = pd.Timestamp("2024-12-30", tz="UTC")  # knowable on the decision date.
    events = pd.DataFrame(
        [
            {
                "event_id": "late01",
                "ticker": "AAA",
                "headline": "Late news",
                "source": "wire",
                "published_at": late,
                "known_at": late,
                "url": "https://example.com/late",
                "sentiment": 0.9,
                "category": "other",
                "importance": 0.8,
            },
            {
                "event_id": "early1",
                "ticker": "AAA",
                "headline": "Early news",
                "source": "wire",
                "published_at": early,
                "known_at": early,
                "url": "https://example.com/early",
                "sentiment": 0.5,
                "category": "other",
                "importance": 0.6,
            },
        ]
    )
    storage = _FakeStorage(events)
    _patch_short(monkeypatch, conviction=CANDIDATE_CONVICTION_THRESHOLD + 1)

    signal = evaluate_short_signal(
        "AAA", _price_frame(), [], storage, dt.date(2025, 1, 1),
        short_model=_FakeShortModel(),
    )
    assert signal is not None
    # event_id is derived (sha256 of ticker|url|source), not the fixture's label.
    kept = [e.event_id for e in
            [EventRecord(ticker="AAA", headline="Early news", source="wire",
                         published_at=early.to_pydatetime(),
                         known_at=early.to_pydatetime(),
                         url="https://example.com/early")]]
    assert signal.event_ids == kept


def test_make_alert_formats_payload() -> None:
    signal = ShortSignal(
        ticker="AAA", conviction=75, recommendation="consider buying",
        top_drivers=["event_sentiment_7d (+)"], event_ids=["abc123"],
        as_of=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
    )
    alert = make_alert(signal)
    assert alert.kind == "short_signal"
    assert alert.ticker == "AAA"
    assert "75" in alert.title
    assert "AAA" in alert.body
    assert "abc123" in alert.detail


def test_run_paper_alerts_delivers_and_debounces(monkeypatch) -> None:
    storage = _FakeStorage()
    notifier = _FakeNotifier()
    prices = _price_frame()
    _patch_short(monkeypatch, conviction=CANDIDATE_CONVICTION_THRESHOLD + 5)

    # First run: alert fires and is recorded for debouncing.
    fired = run_paper_alerts(
        ["AAA"],
        prices_provider=lambda t: prices,
        fundamentals_provider=lambda t: [],
        storage=storage,
        notifier=notifier,
        short_model=_FakeShortModel(),
        as_of=dt.date(2025, 1, 1),
    )
    assert len(fired) == 1
    assert notifier.sent == [(fired[0].title, fired[0].body)]
    assert storage.alerts == [("AAA", "short_signal", fired[0].detail)]

    # Second run: debounced because recent_alert now returns True.
    fired2 = run_paper_alerts(
        ["AAA"],
        prices_provider=lambda t: prices,
        fundamentals_provider=lambda t: [],
        storage=storage,
        notifier=notifier,
        short_model=_FakeShortModel(),
        as_of=dt.date(2025, 1, 1),
    )
    assert fired2 == []
    assert len(notifier.sent) == 1


def test_run_paper_alerts_skips_when_no_short_model() -> None:
    notifier = _FakeNotifier()
    storage = _FakeStorage()
    fired = run_paper_alerts(
        ["AAA"],
        prices_provider=lambda t: pd.DataFrame(),
        fundamentals_provider=lambda t: [],
        storage=storage,
        notifier=notifier,
        short_model=None,
    )
    assert fired == []
    assert notifier.sent == []
