"""Notifier + alert + heartbeat tests (network-free)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from stock_monitor.config import Settings
from stock_monitor.notify import LoggingNotifier, Notifier, get_notifier
from stock_monitor.scan import high_conviction_entrants
from stock_monitor.scheduler import check_heartbeat
from stock_monitor.storage import Storage


class CapturingNotifier(Notifier):
    name = "capture"

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, title: str, body: str) -> bool:
        self.messages.append((title, body))
        return True


def test_logging_notifier_sends() -> None:
    assert LoggingNotifier().send("hi", "there") is True


def test_get_notifier_defaults_to_logging() -> None:
    settings = Settings(telegram_bot_token="", telegram_chat_id="")
    assert get_notifier(settings).name == "logging"


def test_get_notifier_uses_telegram_when_configured() -> None:
    settings = Settings(telegram_bot_token="tok", telegram_chat_id="chat")
    assert get_notifier(settings).name == "telegram"


def test_high_conviction_entrants_only_new_names() -> None:
    previous = [
        {"ticker": "A", "capped_conviction": 80},
        {"ticker": "B", "capped_conviction": 50},
    ]
    current = [
        {"ticker": "A", "capped_conviction": 85, "rank": 1, "recommendation": "buy"},
        {"ticker": "C", "capped_conviction": 75, "rank": 2, "recommendation": "buy"},
        {"ticker": "B", "capped_conviction": 40, "rank": 3, "recommendation": "hold"},
    ]
    entrants = high_conviction_entrants(previous, current, threshold=70)
    assert [e["ticker"] for e in entrants] == ["C"]  # A already high, B below


def test_runs_record_and_read(tmp_path: Path) -> None:
    with Storage(":memory:") as store:
        now = dt.datetime.now()
        store.record_run("universe_scan", "ok", "30 scored", now, now)
        assert store.count("runs") == 1
        last = store.read_last_run("universe_scan")
        assert last is not None and last["status"] == "ok"
        assert store.read_last_run("missing") is None


def test_heartbeat_healthy_and_stale(tmp_path: Path) -> None:
    # Fresh successful run -> healthy, no alert.
    fresh_db = str(tmp_path / "fresh.duckdb")
    fresh_settings = Settings(db_path=fresh_db, heartbeat_max_age_hours=26)
    with Storage(fresh_db) as store:
        now = dt.datetime.now()
        store.record_run("universe_scan", "ok", "ok", now, now)
    notifier = CapturingNotifier()
    assert check_heartbeat(fresh_settings, notifier) is True
    assert notifier.messages == []

    # Only a stale run -> unhealthy, alert fired.
    stale_db = str(tmp_path / "stale.duckdb")
    stale_settings = Settings(db_path=stale_db, heartbeat_max_age_hours=26)
    with Storage(stale_db) as store:
        old = dt.datetime.now() - dt.timedelta(hours=48)
        store.record_run("universe_scan", "ok", "old", old, old)
    stale_notifier = CapturingNotifier()
    assert check_heartbeat(stale_settings, stale_notifier) is False
    assert stale_notifier.messages

    # No run at all -> unhealthy, alert fired.
    empty_settings = Settings(db_path=str(tmp_path / "empty.duckdb"))
    empty_notifier = CapturingNotifier()
    assert check_heartbeat(empty_settings, empty_notifier) is False
    assert empty_notifier.messages


def test_last_alert_detail_returns_most_recent(tmp_path: Path) -> None:
    with Storage(str(tmp_path / "a.duckdb")) as store:
        assert store.last_alert_detail("AAA", "exit_state") is None
        store.record_alert("AAA", "exit_state", "hold")
        store.record_alert("AAA", "exit_state", "consider selling")
        assert store.last_alert_detail("AAA", "exit_state") == "consider selling"
        # Different kind is isolated.
        assert store.last_alert_detail("AAA", "take_profit") is None


def _open_view(**over: object) -> dict:
    base = {
        "status": "open",
        "ticker": "AAA",
        "signal": "hold",
        "current_conviction": 72,
        "entry_conviction": 60,
        "current_flags": [],
        "price_change_pct": 0.05,
        "conviction_change": 12,
        "expert_view": "The thesis still holds — hold.",
    }
    base.update(over)
    return base


def test_holdings_signals_fire_and_debounce(tmp_path: Path, monkeypatch) -> None:
    import stock_monitor.positions as positions
    import stock_monitor.scheduler as sched

    settings = Settings(db_path=str(tmp_path / "h.duckdb"))
    # Patch the scoring seams so no model/network is needed.
    monkeypatch.setattr(
        sched, "_load_scoring_context", lambda s: ("m", "v1", "pp", "fp", "np", "an")
    )
    monkeypatch.setattr(sched, "_daily_return", lambda pp, t: 0.09)  # sharp +9%

    # A holding that is up +25% (take-profit), signal turned to SELL, moving sharply.
    view = _open_view(
        signal="consider selling", current_conviction=30, price_change_pct=0.25
    )
    monkeypatch.setattr(positions, "list_position_views", lambda *a, **k: [view])

    notifier = CapturingNotifier()
    sent = sched.check_holdings_signals(settings, notifier)
    assert sent == 3  # exit->sell + take-profit + sharp move
    joined = " ".join(t for t, _ in notifier.messages)
    assert "AAA" in joined and "selling" in joined

    # Nothing changed on the next run -> everything is debounced.
    again = CapturingNotifier()
    assert sched.check_holdings_signals(settings, again) == 0
    assert again.messages == []


def test_holdings_signals_hold_is_quiet(tmp_path: Path, monkeypatch) -> None:
    import stock_monitor.positions as positions
    import stock_monitor.scheduler as sched

    settings = Settings(db_path=str(tmp_path / "q.duckdb"))
    monkeypatch.setattr(
        sched, "_load_scoring_context", lambda s: ("m", "v1", "pp", "fp", "np", "an")
    )
    monkeypatch.setattr(sched, "_daily_return", lambda pp, t: 0.01)  # calm day

    # A healthy hold, up only a little -> no urgent alert at all.
    monkeypatch.setattr(
        positions, "list_position_views", lambda *a, **k: [_open_view()]
    )
    notifier = CapturingNotifier()
    assert sched.check_holdings_signals(settings, notifier) == 0
    assert notifier.messages == []

