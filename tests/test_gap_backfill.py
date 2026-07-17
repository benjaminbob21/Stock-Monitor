"""Alpha Vantage provider parsing + resumable gap-backfill orchestrator.

All network-free: the provider's HTTP call is monkeypatched and the orchestrator runs
against an in-memory DuckDB with a deterministic keyword analyzer.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from stock_monitor.backfill import backfill_gap_news
from stock_monitor.config import Settings
from stock_monitor.providers.alphavantage_provider import (
    AlphaVantageNewsProvider,
    AlphaVantageRateLimited,
)
from stock_monitor.sentiment import NewsItem, SentimentAnalyzer
from stock_monitor.storage.db import Storage


class _KeywordAnalyzer(SentimentAnalyzer):
    """+1 for 'beats', -1 for 'misses', else 0 (no model download in tests)."""

    name = "keyword"

    def score(self, text: str) -> float:
        low = text.lower()
        if "beats" in low:
            return 1.0
        if "misses" in low:
            return -1.0
        return 0.0


def _item(headline: str, when: dt.datetime | None) -> NewsItem:
    return NewsItem(headline=headline, url="", source="av", published=when)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


# --- provider parsing --------------------------------------------------------


def test_provider_parses_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "feed": [
            {
                "title": "Acme beats estimates",
                "url": "http://x/1",
                "source": "Reuters",
                "time_published": "20240115T133000",
            },
            {
                "title": "Acme misses guidance",
                "url": "http://x/2",
                "source": "Bloomberg",
                "time_published": "20240116T0900",  # no seconds
            },
            {"title": "", "time_published": "20240117T090000"},  # dropped: no title
            {"title": "No date"},  # dropped: no timestamp
        ]
    }
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(payload))

    provider = AlphaVantageNewsProvider("KEY")
    items = provider.get_news_range("acme", dt.date(2024, 1, 1), dt.date(2024, 1, 31))

    assert [i.headline for i in items] == [
        "Acme beats estimates",
        "Acme misses guidance",
    ]
    assert items[0].published == dt.datetime(2024, 1, 15, 13, 30, 0)
    assert items[1].published == dt.datetime(2024, 1, 16, 9, 0)


def test_provider_raises_on_quota_note(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "Information": (
            "Our standard API rate limit is 25 requests per day. "
            "Please subscribe to a premium plan."
        )
    }
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(payload))
    provider = AlphaVantageNewsProvider("KEY")
    with pytest.raises(AlphaVantageRateLimited):
        provider.get_news_range("acme", dt.date(2024, 1, 1), dt.date(2024, 1, 31))


def test_provider_empty_on_unknown_no_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"items": "0"}))
    provider = AlphaVantageNewsProvider("KEY")
    assert (
        provider.get_news_range("acme", dt.date(2024, 1, 1), dt.date(2024, 1, 31)) == []
    )


def test_provider_requires_key() -> None:
    with pytest.raises(ValueError):
        AlphaVantageNewsProvider("")


# --- resumable orchestrator --------------------------------------------------


class _FakeProvider:
    name = "alphavantage-news"

    def __init__(self, by_ticker: dict[str, Any]) -> None:
        self._by_ticker = by_ticker
        self.calls: list[tuple[str, dt.date, dt.date]] = []

    def get_news_range(
        self, ticker: str, from_date: dt.date, to_date: dt.date, *, limit: int = 1000
    ) -> list[NewsItem]:
        self.calls.append((ticker.upper(), from_date, to_date))
        result = self._by_ticker.get(ticker.upper())
        if isinstance(result, Exception):
            raise result
        return list(result or [])


def test_gap_backfill_marks_done_and_is_resumable() -> None:
    settings = Settings(news_backfill_max_per_day=50)
    provider = _FakeProvider(
        {
            "AAA": [_item("AAA beats", dt.datetime(2024, 1, 15, 10))],
            "BBB": [],  # no news in window -> done
        }
    )
    with Storage(":memory:") as store:
        summary = backfill_gap_news(
            settings,
            provider,
            store,
            ["AAA", "BBB"],
            dt.date(2024, 1, 10),
            dt.date(2024, 6, 1),
            analyzer=_KeywordAnalyzer(),
            throttle_seconds=0.0,
        )
        assert summary["calls"] == 2
        assert summary["tickers_done"] == 2
        assert summary["stopped"] == "complete"
        state = store.get_backfill_state("alphavantage-news")
        assert state["AAA"][1] is True
        assert state["BBB"][1] is True

        # Re-run: everything done -> zero provider calls.
        provider2 = _FakeProvider({"AAA": [], "BBB": []})
        summary2 = backfill_gap_news(
            settings,
            provider2,
            store,
            ["AAA", "BBB"],
            dt.date(2024, 1, 10),
            dt.date(2024, 6, 1),
            analyzer=_KeywordAnalyzer(),
            throttle_seconds=0.0,
        )
        assert summary2["calls"] == 0
        assert provider2.calls == []


def test_gap_backfill_respects_daily_cap_and_resumes() -> None:
    settings = Settings()
    provider = _FakeProvider(
        {
            "AAA": [_item("AAA beats", dt.datetime(2024, 1, 15, 10))],
            "BBB": [_item("BBB misses", dt.datetime(2024, 1, 16, 10))],
        }
    )
    with Storage(":memory:") as store:
        summary = backfill_gap_news(
            settings,
            provider,
            store,
            ["AAA", "BBB"],
            dt.date(2024, 1, 10),
            dt.date(2024, 6, 1),
            analyzer=_KeywordAnalyzer(),
            max_calls=1,
            throttle_seconds=0.0,
        )
        assert summary["calls"] == 1
        assert summary["stopped"] == "daily_cap"
        state = store.get_backfill_state("alphavantage-news")
        assert state["AAA"][1] is True
        assert "BBB" not in state  # never reached this run

        summary2 = backfill_gap_news(
            settings,
            provider,
            store,
            ["AAA", "BBB"],
            dt.date(2024, 1, 10),
            dt.date(2024, 6, 1),
            analyzer=_KeywordAnalyzer(),
            max_calls=1,
            throttle_seconds=0.0,
        )
        assert summary2["calls"] == 1
        assert store.get_backfill_state("alphavantage-news")["BBB"][1] is True


def test_gap_backfill_stops_on_rate_limit() -> None:
    settings = Settings()
    provider = _FakeProvider({"AAA": AlphaVantageRateLimited("quota")})
    with Storage(":memory:") as store:
        summary = backfill_gap_news(
            settings,
            provider,
            store,
            ["AAA", "BBB"],
            dt.date(2024, 1, 10),
            dt.date(2024, 6, 1),
            analyzer=_KeywordAnalyzer(),
            throttle_seconds=0.0,
        )
        assert summary["stopped"] == "rate_limited"
        assert summary["calls"] == 0
        assert store.get_backfill_state("alphavantage-news") == {}


def test_gap_backfill_resumes_after_capped_response() -> None:
    settings = Settings(news_backfill_max_per_day=50)
    # A full 1000-item response means the window was truncated -> not done; resume from
    # the last article's day next run.
    items = [_item(f"AAA beats {i}", dt.datetime(2024, 1, 15, 10)) for i in range(999)]
    items.append(_item("AAA beats last", dt.datetime(2024, 2, 20, 10)))
    provider = _FakeProvider({"AAA": items})
    with Storage(":memory:") as store:
        summary = backfill_gap_news(
            settings,
            provider,
            store,
            ["AAA"],
            dt.date(2024, 1, 10),
            dt.date(2024, 6, 1),
            analyzer=_KeywordAnalyzer(),
            throttle_seconds=0.0,
        )
        assert summary["tickers_done"] == 0
        assert store.get_backfill_state("alphavantage-news")["AAA"] == (
            dt.date(2024, 2, 20),
            False,
        )

        # Next run resumes the day after the covered-through date.
        provider.calls.clear()
        provider._by_ticker["AAA"] = []  # now no more news -> done
        backfill_gap_news(
            settings,
            provider,
            store,
            ["AAA"],
            dt.date(2024, 1, 10),
            dt.date(2024, 6, 1),
            analyzer=_KeywordAnalyzer(),
            throttle_seconds=0.0,
        )
        assert provider.calls[0][1] == dt.date(2024, 2, 21)
        assert store.get_backfill_state("alphavantage-news")["AAA"][1] is True
