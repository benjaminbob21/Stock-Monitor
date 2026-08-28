"""Alternative-sentiment collectors: Reddit + financial-media RSS.

Approved design (2026-08-27): alt-sentiment is collected in ONE daily batch on the VM
scheduler — never on-demand — then served from DuckDB so UI reads are instant and free.

Sources (all free, ToS-respecting):
- Reddit: official OAuth API, personal-use script app (REDDIT_CLIENT_ID/SECRET in .env).
  Pulls the latest posts from a few subreddits ONCE per run (3 requests total, not
  per-ticker searches) and matches $TICKER/keyword mentions locally.
- Media RSS: CNBC, Motley Fool, Forbes, Yahoo Finance. Headlines only, same shape as
  the news pipeline.

Scoring reuses the SAME FinBERT analyzer as the news pipeline, so alt-sentiment lives
on the identical scale (soft polarity p(pos)−p(neg)) and is directly comparable.

Tables: ``alt_sentiment`` (daily per-ticker aggregate, the feature source) and
``alt_posts`` (raw archive so we can re-score when scoring changes). Bearer-token
Reddit OAuth login is cached until expiry (token is ~24h).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

from stock_monitor.config import Settings
from stock_monitor.storage.db import Storage
from stock_monitor.universe import get_scan_universe

logger = logging.getLogger("stock_monitor.alt_sentiment")

REDDIT_SUBREDDITS = ("wallstreetbets", "stocks", "investing")
_REDDIT_OAUTH_URL = "https://www.reddit.com/api/v1/access_token"
_REDDIT_NEW_URL = "https://oauth.reddit.com/r/{sub}/new"

MEDIA_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("cnbc", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("wsj-markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("investing", "https://www.investing.com/rss/news.rss"),
    ("yahoo", "https://finance.yahoo.com/news/rssindex"),
)

_BIWEEKLY_CRON_DAY = "1,15"

_LLM_SYSTEM_PROMPT = """You are the market-chatter analyst for a personal stock portfolio tool.
You are given a raw batch of recent Reddit finance posts and financial-media headlines.
Identify every clearly-identifiable publicly-traded company/ETF being discussed (match
casual names, misspellings, $CASHTAGS, and tickers yourself). Ignore generic market talk
with no specific ticker. For each ticker return:
- sentiment: number in [-1, 1] where -1 = crowd/outlets are bearish, +1 = bullish
- buzz: integer 0-10 for how much attention it is getting (0 = one stray mention)
- summary: ONE short sentence (max 20 words) capturing the dominant narrative
Only include tickers from the ALLOWED LIST if one is provided. Respond with JSON only:
{"tickers": [{"ticker": "NVDA", "sentiment": 0.4, "buzz": 7, "summary": "..."}]}
If nothing identifiable is discussed, return {"tickers": []}."""


class RedditClient:
    """Minimal Reddit OAuth client (script app, personal use — free 100 QPM tier)."""

    def __init__(self, client_id: str, client_secret: str, user_agent: str) -> None:
        self._auth = (client_id, client_secret)
        self._user_agent = user_agent
        self._token: str | None = None
        self._token_exp: float = 0.0

    def _bearer(self) -> str | None:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        import requests

        try:
            resp = requests.post(
                _REDDIT_OAUTH_URL,
                auth=self._auth,
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": self._user_agent},
                timeout=15,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:  # noqa: BLE001 — collector must never crash the scheduler
            logger.exception("reddit oauth failed")
            return None
        self._token = str(payload.get("access_token", "")) or None
        self._token_exp = time.time() + float(payload.get("expires_in", 3600))
        return self._token

    def fetch_subreddit_new(self, subreddit: str, limit: int = 100) -> list[dict]:
        """Latest posts from one subreddit: title + permalink + score + created time."""
        token = self._bearer()
        if not token:
            return []
        import requests

        try:
            resp = requests.get(
                _REDDIT_NEW_URL.format(sub=subreddit),
                params={"limit": limit},
                headers={
                    "Authorization": f"bearer {token}",
                    "User-Agent": self._user_agent,
                },
                timeout=15,
            )
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", [])
        except Exception:  # noqa: BLE001
            logger.exception("reddit fetch failed for r/%s", subreddit)
            return []
        posts: list[dict] = []
        for child in children:
            d = child.get("data", {})
            title = d.get("title")
            if not title:
                continue
            created = d.get("created_utc")
            posts.append(
                {
                    "source": f"reddit:{subreddit}",
                    "text": title,
                    "url": f"https://reddit.com{d.get('permalink', '')}",
                    "published": (dt.datetime.fromtimestamp(created) if created else None),
                    "engagement": int(d.get("score", 0)),
                }
            )
        return posts

    def fetch_all(
        self,
        subreddits: tuple[str, ...] = REDDIT_SUBREDDITS,
        limit: int = 100,
    ) -> list[dict]:
        posts: list[dict] = []
        for sub in subreddits:
            posts.extend(self.fetch_subreddit_new(sub, limit))
        return posts


def fetch_media_rss(
    feeds: tuple[tuple[str, str], ...] = MEDIA_RSS_FEEDS,
) -> list[dict]:
    """Pull headline items from financial-media RSS feeds (std-lib XML, no new dep)."""
    import xml.etree.ElementTree as ET

    import requests

    posts: list[dict] = []
    for name, url in feeds:
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "stock-monitor/1.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception:  # noqa: BLE001 — a dead feed must not kill the batch
            logger.exception("rss fetch failed for %s", name)
            continue
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            title = _safe_text(title_el)
            if not title:
                continue
            published = None
            if pub_el is not None and pub_el.text:
                try:
                    from email.utils import parsedate_to_datetime

                    published = parsedate_to_datetime(pub_el.text.strip()).replace(tzinfo=None)
                except Exception:  # noqa: BLE001 — date format drift is non-fatal
                    pass
            posts.append(
                {
                    "source": f"rss:{name}",
                    "text": title,
                    "url": _safe_text(link_el),
                    "published": published,
                    "engagement": 0,
                }
            )
    return posts


def _safe_text(el: Any) -> str:
    return (el.text or "").strip() if el is not None and el.text else ""


def _llm_read_batch(posts: list[dict], settings: Settings) -> list[dict]:
    """One LLM call over the whole raw batch → per-ticker verdicts.

    The LLM only CLASSIFIES chatter; the allocation engine still owns all weight math.
    Returns a list of dicts: ticker, sentiment, buzz, summary.
    """
    import json

    import requests

    if not settings.openrouter_api_key:
        logger.warning("no openrouter key; skipping LLM alt-sentiment read")
        return []

    lines = [
        f"[{p['source']}] {p['text']}"
        + (f" (upvotes: {p['engagement']})" if p["engagement"] else "")
        for p in posts
    ]
    body: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
    }
    try:
        resp = requests.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://stock-monitor.vercel.app",
                "X-Title": "Stock Monitor",
            },
            json=body,
            timeout=120,
        )
        resp.raise_for_status()
        parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001 — collector must never crash the scheduler
        logger.exception("LLM alt-sentiment batch read failed")
        return []

    verdicts: list[dict] = []
    for row in parsed.get("tickers", []):
        try:
            verdicts.append(
                {
                    "ticker": str(row["ticker"]).upper().strip(),
                    "sentiment": max(-1.0, min(1.0, float(row["sentiment"]))),
                    "buzz": max(0, min(10, int(row["buzz"]))),
                    "summary": str(row.get("summary", ""))[:300],
                }
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("skipping malformed LLM verdict row: %r", row)
    return verdicts


def collect_alt_sentiment(settings: Settings) -> int:
    """Biweekly batch: ping Reddit + RSS once, let the LLM read the raw batch.

    Cadence (approved 2026-08-28): every 2 weeks, matching the user's trade cycle.
    Fetching is tiny (3 subreddit pulls + 4 RSS feeds); an LLM call classifies the
    whole batch into per-ticker sentiment/buzz/summary — no regex or FinBERT.
    The verdicts land in ``alt_sentiment`` (per ticker) and the raw batch in
    ``alt_posts`` (audit trail, tickers NULL until matched by the LLM).
    Returns the number of tickers the LLM classified.
    """

    posts: list[dict] = []
    if settings.reddit_client_id and settings.reddit_client_secret:
        reddit = RedditClient(
            settings.reddit_client_id,
            settings.reddit_client_secret,
            settings.reddit_user_agent,
        )
        posts.extend(reddit.fetch_all())
    else:
        logger.warning("reddit credentials missing; collecting media RSS only")
    posts.extend(fetch_media_rss(MEDIA_RSS_FEEDS))
    if not posts:
        logger.warning("alt-sentiment batch found nothing to read")
        return 0

    verdicts = _llm_read_batch(posts, settings)

    with Storage(settings.db_path) as storage:
        # Raw archive first (audit trail); ticker left NULL when the LLM didn't cite it.
        storage.record_alt_posts(posts)
        if verdicts:
            allowed: set[str] | None = None
            try:
                allowed = set(get_scan_universe(settings)) | {
                    p["ticker"] for p in storage.list_positions() if p["status"] == "open"
                }
            except Exception:  # noqa: BLE001 — universe lookup is best-effort
                logger.exception("universe lookup failed; storing verdicts unfiltered")
                allowed = None
            rows = [
                {
                    "ticker": v["ticker"],
                    "date": dt.date.today(),
                    "sentiment": v["sentiment"],
                    "buzz": v["buzz"],
                    "summary": v["summary"],
                    "backend": f"llm:{settings.llm_model}",
                }
                for v in verdicts
                if allowed is None or v["ticker"] in allowed
            ]
            storage.upsert_alt_sentiment_llm(rows)
    logger.info(
        "alt-sentiment batch: %d raw posts, %d tickers classified", len(posts), len(verdicts)
    )
    return len(verdicts)
