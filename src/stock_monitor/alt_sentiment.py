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
import re
import time
from typing import Any

import pandas as pd

from stock_monitor.config import Settings
from stock_monitor.sentiment import get_sentiment_analyzer
from stock_monitor.storage.db import Storage
from stock_monitor.universe import get_scan_universe

logger = logging.getLogger("stock_monitor.alt_sentiment")

REDDIT_SUBREDDITS = ("wallstreetbets", "stocks", "investing")
_REDDIT_OAUTH_URL = "https://www.reddit.com/api/v1/access_token"
_REDDIT_NEW_URL = "https://oauth.reddit.com/r/{sub}/new"

MEDIA_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("cnbc", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01"),
    ("motleyfool", "https://www.fool.com/feeds/rss/news.xml"),
    ("forbes", "https://www.forbes.com/money/feed/"),
    ("yahoo", "https://finance.yahoo.com/news/rssindex"),
)

# Ticker tokens we must never match even if they appear like $SYMBOL.
_TICKER_STOP = {
    "DD",
    "YOLO",
    "LOL",
    "ITM",
    "OTM",
    "ATH",
    "IPO",
    "CEO",
    "CFO",
    "EPS",
    "GDP",
    "FED",
    "APR",
    "JAN",
    "FEB",
    "MAR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
    "USA",
    "UK",
    "EU",
    "AI",
    "WSB",
}

_TICKER_RE = re.compile(r"\$([A-Z]{1,5})\b|\b([A-Z]{2,5})\b")


def _safe_text(el: Any) -> str:
    return (el.text or "").strip() if el is not None and el.text else ""


def _match_company_names(text: str, universe: set[str], names: dict[str, str]) -> set[str]:
    """Map full company names in ``text`` to tickers, restricted to ``universe``.

    ``names`` maps NAME → ticker. Names longer than one word require a whole-phrase
    hit so "apple" in a recipe context doesn't fire — only AAPL-eligible names.
    """
    up = f" {text.upper()} "
    found: set[str] = set()
    for name, ticker in names.items():
        if ticker not in universe:
            continue
        if " " in name:
            if f" {name} " in up:
                found.add(ticker)
        elif re.search(rf"\b{re.escape(name)}\b", up):
            found.add(ticker)
    return found


def _match_tickers(text: str, universe: set[str]) -> set[str]:
    """Extract mentioned tickers: $CASHTAGS always count; bare words only if in-universe."""
    found: set[str] = set()
    for cash, word in _TICKER_RE.findall(text.upper()):
        tok = cash or word
        if tok in _TICKER_STOP:
            continue
        if cash or tok in universe:
            found.add(tok)
    return found


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
                    "published": dt.datetime.fromtimestamp(created) if created else None,
                    "engagement": int(d.get("score", 0)),
                }
            )
        return posts

    def fetch_all(
        self, subreddits: tuple[str, ...] = REDDIT_SUBREDDITS, limit: int = 100
    ) -> list[dict]:
        posts: list[dict] = []
        for sub in subreddits:
            posts.extend(self.fetch_subreddit_new(sub, limit))
        return posts


def fetch_media_rss(feeds: tuple[tuple[str, str], ...] = MEDIA_RSS_FEEDS) -> list[dict]:
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
            title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
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


def collect_alt_sentiment(settings: Settings, *, limit_per_sub: int = 100) -> int:
    """One batch: fetch Reddit + RSS, match tickers, FinBERT-score, store aggregates.

    Universe = scan universe + holdings + latest opportunities (same as news collect).
    Returns the number of ``alt_posts`` rows archived. Safe to re-run; the tables are
    idempotent on (source-url) / (ticker, date).
    """
    from stock_monitor.symbols import SymbolDirectory

    reddit = None
    if settings.reddit_client_id and settings.reddit_client_secret:
        reddit = RedditClient(
            settings.reddit_client_id,
            settings.reddit_client_secret,
            settings.reddit_user_agent,
        )
    else:
        logger.warning("reddit credentials missing; collecting media RSS only")

    posts: list[dict] = []
    if reddit is not None:
        posts.extend(reddit.fetch_all(limit=limit_per_sub))
    posts.extend(fetch_media_rss(MEDIA_RSS_FEEDS))
    if not posts:
        logger.warning("alt-sentiment batch found nothing to score")
        return 0

    analyzer = get_sentiment_analyzer(settings)
    with Storage(settings.db_path) as storage:
        holdings = {p["ticker"] for p in storage.list_positions() if p["status"] == "open"}
        opportunities = {o["ticker"] for o in storage.read_latest_opportunities(limit=50)}
        universe = {*get_scan_universe(settings), *holdings, *opportunities}

        # Media headlines say "Nvidia", not "NVDA" — add name→ticker matching from
        # the SEC registry (cached HTTP; falls back to token matching only).
        names: dict[str, str] = {}
        try:
            by_ticker = SymbolDirectory()._load()
            names = {
                name.upper(): ticker
                for ticker, name in by_ticker.items()
                if name and 3 <= len(name) <= 40
            }
        except Exception:  # noqa: BLE001 — name matching is best-effort
            logger.exception("company-name map unavailable; token matching only")

        rows: list[dict] = []
        for post in posts:
            mentioned = _match_tickers(post["text"], universe)
            mentioned |= _match_company_names(post["text"], universe, names)
            for ticker in mentioned:
                rows.append({**post, "ticker": ticker})
        if not rows:
            return 0

        scores = analyzer.score_batch([r["text"] for r in rows])
        for r, s in zip(rows, scores, strict=True):
            r["sentiment"] = s
            r["backend"] = getattr(analyzer, "name", "unknown")
        frame = pd.DataFrame(rows)

        archive = frame.rename(columns={"text": "headline"})[
            [
                "ticker",
                "published",
                "headline",
                "source",
                "url",
                "sentiment",
                "backend",
                "engagement",
            ]
        ]
        archived = storage.upsert_alt_posts(archive)

        daily = _aggregate_daily(frame)
        storage.upsert_alt_sentiment(daily)
    logger.info(
        "alt-sentiment batch: %d posts, %d ticker rows archived, %d daily aggregates",
        len(posts),
        archived,
        len(daily),
    )
    return archived


def _aggregate_daily(frame: pd.DataFrame) -> pd.DataFrame:
    """Engagement-weighted daily sentiment per ticker.

    Reddit posts carry upvote scores; RSS carries none (weight 1), so a loud Reddit
    thread outweighs a quiet one. A ticker with no coverage gets NO row (absence is
    neutral by convention, same as news).
    """
    f = frame.copy()
    f["date"] = pd.to_datetime(f["published"], errors="coerce").dt.date
    # Undated rows can't anchor a daily aggregate (same rule as news articles).
    f = f.dropna(subset=["date", "sentiment"])
    if f.empty:
        return pd.DataFrame(columns=["ticker", "date", "sentiment", "post_count", "backend"])
    f["weight"] = f["engagement"].clip(lower=0).to_numpy() + 1.0
    grouped = f.groupby("ticker", as_index=False).apply(
        lambda g: pd.Series(
            {
                "date": g.loc[g.index[0], "date"],
                "sentiment": (g["sentiment"] * g["weight"]).sum() / g["weight"].sum(),
                "post_count": int(len(g)),
                "backend": "finbert",
            }
        ),
        include_groups=False,
    )
    return grouped
