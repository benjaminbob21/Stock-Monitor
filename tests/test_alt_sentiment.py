"""Tests for the alt-sentiment collectors (Reddit OAuth + media RSS), HTTP mocked."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from stock_monitor.alt_sentiment import (
    RedditClient,
    _llm_read_batch,
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

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(None, content=RSS_XML))
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


def test_llm_read_batch_parses_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["body"] = kwargs["json"]
        content = (
            '{"tickers": ['
            '{"ticker": "nvda", "sentiment": 0.9, "buzz": 99, "summary": "moon"},'
            '{"ticker": "BAD", "sentiment": "x", "buzz": 1, "summary": "bad row"},'
            '{"ticker": "TSLA", "sentiment": -2.0, "buzz": 3, "summary": "clamped"}'
            "]}"
        )
        return _FakeResponse({"choices": [{"message": {"content": content}}]})

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    settings = Settings(openrouter_api_key="k", llm_model="m")
    posts = [
        {"source": "reddit:wallstreetbets", "text": "$NVDA to the moon", "engagement": 42},
        {"source": "rss:cnbc", "text": "TSLA cuts prices", "engagement": 0},
    ]
    verdicts = _llm_read_batch(posts, settings)
    assert captured["url"].endswith("/chat/completions")
    assert verdicts == [
        {"ticker": "NVDA", "sentiment": 0.9, "buzz": 10, "summary": "moon"},
        {"ticker": "TSLA", "sentiment": -1.0, "buzz": 3, "summary": "clamped"},
    ]


def test_llm_read_batch_requires_key() -> None:
    assert _llm_read_batch([{"source": "s", "text": "x", "engagement": 0}], Settings()) == []


def test_llm_read_batch_api_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("502")

    monkeypatch.setattr(requests, "post", boom)
    settings = Settings(openrouter_api_key="k")
    assert _llm_read_batch([{"source": "s", "text": "x", "engagement": 0}], settings) == []


def test_collect_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    import requests

    def fake_post(url: str, **__: Any) -> _FakeResponse:
        if (
            "oauth.reddit.com" not in url
            and "access_token" not in url
            and "api/v1/access_token" not in url
        ):
            content = (
                '{"tickers": [{"ticker": "NVDA", "sentiment": 0.5, "buzz": 6, "summary": "hype"}]}'
            )
            return _FakeResponse({"choices": [{"message": {"content": content}}]})
        if "token" in url:
            return _FakeResponse({"access_token": "t", "expires_in": 3600})
        return _FakeResponse(_reddit_children("$NVDA moon"))

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(None, content=RSS_XML))

    import stock_monitor.alt_sentiment as mod

    settings = Settings(
        db_path=str(tmp_path / "t.duckdb"),
        reddit_client_id="id",
        reddit_client_secret="sec",
        openrouter_api_key="k",
    )
    monkeypatch.setattr(mod, "MEDIA_RSS_FEEDS", (("cnbc", "https://x/rss"),))
    monkeypatch.setattr(mod, "get_scan_universe", lambda *_: ["NVDA", "AAPL"])

    real_storage = mod.Storage(settings.db_path)
    monkeypatch.setattr(mod, "Storage", lambda _p: real_storage)
    monkeypatch.setattr(type(real_storage), "__exit__", lambda self, *exc: None)
    # Open positions list used for the allowed-universe filter.
    monkeypatch.setattr(real_storage, "list_positions", lambda: [])

    classified = collect_alt_sentiment(settings)
    assert classified == 1

    daily = real_storage.read_alt_sentiment()
    assert set(daily["ticker"]) == {"NVDA"}
    row = daily.iloc[0]
    assert row["buzz"] == 6
    assert row["backend"].startswith("llm:")
    real_storage.close()


def test_collect_without_reddit_still_reads_rss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import requests

    def no_token_post(*_: Any, **__: Any) -> Any:
        raise AssertionError("no reddit token calls expected")

    monkeypatch.setattr(requests, "post", no_token_post)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(None, content=RSS_XML))

    import stock_monitor.alt_sentiment as mod

    settings = Settings(db_path=str(tmp_path / "t.duckdb"), openrouter_api_key="k")
    monkeypatch.setattr(mod, "MEDIA_RSS_FEEDS", (("cnbc", "https://x/rss"),))
    monkeypatch.setattr(mod, "get_scan_universe", lambda *_: ["NVDA", "AAPL"])

    real_storage = mod.Storage(settings.db_path)
    monkeypatch.setattr(mod, "Storage", lambda _p: real_storage)
    monkeypatch.setattr(type(real_storage), "__exit__", lambda self, *exc: None)
    monkeypatch.setattr(real_storage, "list_positions", lambda: [])
    # LLM read via requests.post is blocked above → verdicts [] but raw posts stored.
    assert collect_alt_sentiment(settings) == 0
    raw = real_storage._con.execute("select count(*) from alt_posts").fetchone()
    assert raw[0] == 2
    real_storage.close()
