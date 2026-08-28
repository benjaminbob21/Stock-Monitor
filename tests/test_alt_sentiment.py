"""Tests for the alt-sentiment collectors (Reddit OAuth + media RSS), HTTP mocked."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest

from stock_monitor.alt_sentiment import (
    RedditClient,
    _aggregate_daily,
    _match_tickers,
    collect_alt_sentiment,
    fetch_media_rss,
)
from stock_monitor.config import Settings


class _FakeResponse:
    def __init__(self, payload: Any, content: bytes = b"") -> None:
        self._payload = payload
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload

    @property
    def content(self) -> bytes:
        return self._content


def _reddit_children(*titles: str) -> dict[str, Any]:
    return {
        "data": {
            "children": [
                {
                    "data": {
                        "title": t,
                        "permalink": f"/r/wallstreetbets/comments/{i}",
                        "score": 10 + i,
                        "created_utc": 1_760_000_000 + i * 3600,
                    }
                }
                for i, t in enumerate(titles)
            ]
        }
    }


RSS_XML = b"""<?xml version="1.0"?>
<rss><channel>
<item><title>NVDA rallies again</title><link>https://e.example/nvda</link>
<pubDate>Tue, 26 Aug 2026 10:00:00 GMT</pubDate></item>
<item><title>AAPL dips on supply fears</title><link>https://e.example/aapl</link>
<pubDate>Tue, 26 Aug 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""


class _FakeAnalyzer:
    name = "finbert-fake"

    def score_batch(self, texts: list[str]) -> list[float]:
        return [0.5 if "rallies" in t or "moon" in t else -0.5 for t in texts]


@pytest.fixture()
def _universe(monkeypatch: pytest.MonkeyPatch) -> None:
    import stock_monitor.alt_sentiment as mod

    monkeypatch.setattr(mod, "get_scan_universe", lambda *_: ["NVDA", "AAPL"])


def test_match_tickers_cashtags_and_universe_words() -> None:
    text = "$NVDA to the moon, also discussed AAPL and YOLO and GPU"
    assert _match_tickers(text, {"NVDA", "AAPL"}) == {"NVDA", "AAPL"}


def test_match_tickers_ignores_unknown_words() -> None:
    assert _match_tickers("GPU holders DD thread", {"NVDA"}) == set()


def test_reddit_client_fetch_and_token_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, **_: Any) -> _FakeResponse:
        calls.append("token")
        return _FakeResponse({"access_token": "t0k", "expires_in": 3600})

    def fake_get(url: str, **_: Any) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(_reddit_children("$NVDA moon", "AAPL silent"))

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    client = RedditClient("id", "secret", "ua")
    posts = client.fetch_all(limit=100)
    assert len(posts) == 6  # 2 posts × 3 subreddits
    assert posts[0]["source"] == "reddit:wallstreetbets"
    assert posts[0]["engagement"] == 10
    assert posts[0]["published"] is not None
    # Second call must reuse the cached token (no new "token" call).
    client.fetch_all(limit=100)
    assert calls.count("token") == 1
    # 3 subreddit pulls per fetch_all × 2 fetch_all calls = 6.
    assert sum(1 for c in calls if c.startswith("https://oauth")) == 6


def test_reddit_client_bad_credentials_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("401")

    monkeypatch.setattr(requests, "post", boom)
    assert RedditClient("id", "secret", "ua").fetch_all() == []


def test_fetch_media_rss_parses_feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    monkeypatch.setattr(
        requests, "get", lambda *a, **k: _FakeResponse(None, content=RSS_XML)
    )
    items = fetch_media_rss(feeds=(("cnbc", "https://x/rss"),))
    assert len(items) == 2
    assert items[0]["source"] == "rss:cnbc"
    assert items[0]["url"] == "https://e.example/nvda"
    assert items[0]["published"] == dt.datetime(2026, 8, 26, 10, 0)


def test_fetch_media_rss_dead_feed_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("timeout")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_media_rss(feeds=(("cnbc", "https://x/rss"),)) == []


def test_aggregate_daily_engagement_weights() -> None:
    frame = pd.DataFrame(
        [
            {"ticker": "NVDA", "published": dt.datetime(2026, 8, 26, 9),
             "sentiment": 1.0, "engagement": 99, "source": "reddit:wallstreetbets"},
            {"ticker": "NVDA", "published": dt.datetime(2026, 8, 26, 10),
             "sentiment": -1.0, "engagement": 1, "source": "reddit:stocks"},
            {"ticker": "NVDA", "published": dt.datetime(2026, 8, 26, 11),
             "sentiment": 0.4, "engagement": 0, "source": "rss:cnbc"},
        ]
    )
    daily = _aggregate_daily(frame)
    row = daily[daily["ticker"] == "NVDA"].iloc[0]
    # (1.0*100 + -1.0*2 + 0.4*1) / 103
    assert row["sentiment"] == pytest.approx((100 - 2 + 0.4) / 103)
    assert row["post_count"] == 3


def test_collect_end_to_end(
    monkeypatch: pytest.MonkeyPatch, _universe: None, tmp_path: Any
) -> None:
    def fake_post(*_: Any, **__: Any) -> _FakeResponse:
        return _FakeResponse({"access_token": "t", "expires_in": 3600})

    def fake_get(url: str, **__: Any) -> _FakeResponse:
        if "oauth.reddit.com" in url:
            return _FakeResponse(_reddit_children("$NVDA moon"))
        return _FakeResponse(None, content=RSS_XML)

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)

    settings = Settings(db_path=str(tmp_path / "t.duckdb"), sentiment_backend="vader",
                        reddit_client_id="id", reddit_client_secret="sec")

    import stock_monitor.alt_sentiment as mod

    monkeypatch.setattr(mod, "get_sentiment_analyzer", lambda _s: _FakeAnalyzer())
    monkeypatch.setattr(mod, "MEDIA_RSS_FEEDS", (("cnbc", "https://x/rss"),))

    real_storage = mod.Storage(settings.db_path)
    monkeypatch.setattr(mod, "Storage", lambda _p: real_storage)
    # collect uses `with Storage(...)` which closes on exit; keep it open for reads.
    monkeypatch.setattr(
        type(real_storage), "__exit__", lambda self, *exc: None
    )

    archived = collect_alt_sentiment(settings)
    assert archived == 5  # 3× "$NVDA moon" (one per sub) + NVDA/AAPL rss items

    daily = real_storage.read_alt_sentiment()
    assert {"NVDA", "AAPL"} <= set(daily["ticker"])
    assert (daily["post_count"] >= 1).all()
    real_storage.close()


def test_collect_without_reddit_still_collects_rss(
    monkeypatch: pytest.MonkeyPatch, _universe: None, tmp_path: Any
) -> None:
    import requests

    def no_post(*_: Any, **__: Any) -> Any:
        raise AssertionError("no reddit token calls expected")

    monkeypatch.setattr(requests, "post", no_post)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(None, content=RSS_XML))

    import stock_monitor.alt_sentiment as mod

    settings = Settings(db_path=str(tmp_path / "t.duckdb"), sentiment_backend="vader")
    monkeypatch.setattr(mod, "get_sentiment_analyzer", lambda _s: _FakeAnalyzer())
    monkeypatch.setattr(mod, "MEDIA_RSS_FEEDS", (("cnbc", "https://x/rss"),))

    real_storage = mod.Storage(settings.db_path)
    monkeypatch.setattr(mod, "Storage", lambda _p: real_storage)
    monkeypatch.setattr(
        type(real_storage), "__exit__", lambda self, *exc: None
    )

    archived = collect_alt_sentiment(settings)
    assert archived == 2  # only the RSS items

    daily = real_storage.read_alt_sentiment()
    assert set(daily["ticker"]) == {"NVDA", "AAPL"}
    real_storage.close()
