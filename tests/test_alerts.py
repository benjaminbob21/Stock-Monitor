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
