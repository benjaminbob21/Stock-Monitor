"""News + sentiment pillar (build-plan §5, §7 Phase 4).

Fetches recent headlines per ticker and scores their sentiment, behind two swappable
seams (same pattern as data providers):

- ``NewsProvider``      : yfinance (no key, default) or Finnhub (key-gated).
- ``SentimentAnalyzer`` : VADER + a finance-term lexicon boost (default, zero heavy
                          deps) or FinBERT (drop-in upgrade via the ``finbert`` extra).

Important honesty note: we have no *historical* news, so sentiment can't be baked into
the trained ``sentiment`` feature (it stays 0.0 at train time). Instead sentiment is a
**live overlay** — shown alongside a score and, on held positions, used to trigger a
news-driven sell (build-plan §5 exit rules).
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from stock_monitor.config import Settings

# A small finance-term boost so the general VADER lexicon reads headlines sensibly.
# (Values are on VADER's ~[-4, 4] per-token scale.)
_FINANCE_LEXICON: dict[str, float] = {
    "beats": 2.5, "beat": 2.0, "tops": 1.8, "surge": 2.5, "surges": 2.5, "soars": 3.0,
    "soar": 3.0, "rally": 2.0, "rallies": 2.0, "jumps": 2.0, "jumped": 2.0, "gains": 1.5,
    "upgrade": 2.5, "upgraded": 2.5, "outperform": 2.0, "record": 1.5, "raises": 1.5,
    "strong": 1.5, "growth": 1.0, "profit": 1.2, "rebounds": 2.0, "buyback": 1.5,
    "slashes": -2.5, "slash": -2.0, "cuts": -1.5, "plunge": -3.0, "plunges": -3.0,
    "plummet": -3.0, "tumble": -2.5, "tumbles": -2.5, "sinks": -2.0, "drops": -1.5,
    "falls": -1.5, "probe": -2.0, "lawsuit": -2.0, "sues": -2.0, "fraud": -3.5,
    "investigation": -2.0, "downgrade": -2.5, "downgraded": -2.5, "miss": -2.0,
    "misses": -2.0, "warns": -2.0, "warning": -1.5, "recall": -2.0, "layoffs": -2.0,
    "bankruptcy": -3.5, "halts": -2.0, "weak": -1.5, "loss": -1.5, "guidance": 0.0,
}

_POS_LABEL = 0.15
_NEG_LABEL = -0.15


@dataclass
class NewsItem:
    headline: str
    url: str
    source: str
    published: dt.datetime | None
    sentiment: float | None = None


@dataclass
class SentimentReport:
    ticker: str
    score: float  # mean sentiment in [-1, 1]
    label: str  # positive | neutral | negative
    count: int
    backend: str
    items: list[NewsItem] = field(default_factory=list)


class NewsProvider(ABC):
    name: str

    @abstractmethod
    def get_news(self, ticker: str, lookback_days: int) -> list[NewsItem]:
        raise NotImplementedError


class YFinanceNewsProvider(NewsProvider):
    """Recent headlines from Yahoo Finance (no API key). Defensive across formats."""

    name = "yfinance-news"

    def get_news(self, ticker: str, lookback_days: int) -> list[NewsItem]:
        import yfinance as yf

        try:
            raw = yf.Ticker(ticker).news or []
        except Exception:  # noqa: BLE001 — news is optional; degrade to none
            return []

        cutoff = dt.datetime.now() - dt.timedelta(days=lookback_days)
        items: list[NewsItem] = []
        for entry in raw:
            item = _parse_yahoo(entry)
            if item and (item.published is None or item.published >= cutoff):
                items.append(item)
        return items


class FinnhubNewsProvider(NewsProvider):
    """Company news from Finnhub (free tier, requires an API key)."""

    name = "finnhub-news"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def get_news(self, ticker: str, lookback_days: int) -> list[NewsItem]:
        import requests

        today = dt.date.today()
        start = today - dt.timedelta(days=lookback_days)
        try:
            resp = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={
                    "symbol": ticker.upper(),
                    "from": start.isoformat(),
                    "to": today.isoformat(),
                    "token": self._api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:  # noqa: BLE001
            return []

        items: list[NewsItem] = []
        for entry in data:
            headline = entry.get("headline")
            if not headline:
                continue
            ts = entry.get("datetime")
            published = dt.datetime.fromtimestamp(ts) if ts else None
            items.append(
                NewsItem(
                    headline=headline,
                    url=entry.get("url", ""),
                    source=entry.get("source", "finnhub"),
                    published=published,
                )
            )
        return items


def _parse_yahoo(entry: dict) -> NewsItem | None:
    # Newer yfinance nests under "content"; older is flat.
    content = entry.get("content", entry)
    headline = content.get("title")
    if not headline:
        return None

    url = ""
    canonical = content.get("canonicalUrl")
    if isinstance(canonical, dict):
        url = canonical.get("url", "")
    url = url or content.get("link", "")

    source = ""
    provider = content.get("provider")
    if isinstance(provider, dict):
        source = provider.get("displayName", "")
    source = source or content.get("publisher", "Yahoo")

    published: dt.datetime | None = None
    if "providerPublishTime" in content:
        try:
            published = dt.datetime.fromtimestamp(int(content["providerPublishTime"]))
        except (TypeError, ValueError, OSError):
            published = None
    elif content.get("pubDate"):
        try:
            published = dt.datetime.fromisoformat(str(content["pubDate"]).replace("Z", "+00:00"))
            published = published.replace(tzinfo=None)
        except ValueError:
            published = None

    return NewsItem(headline=headline, url=url, source=source, published=published)


class SentimentAnalyzer(ABC):
    name: str

    @abstractmethod
    def score(self, text: str) -> float:
        """Return sentiment in [-1, 1]."""
        raise NotImplementedError


class VaderAnalyzer(SentimentAnalyzer):
    """VADER with a finance-term lexicon boost (default; no heavy deps)."""

    name = "vader"

    def __init__(self) -> None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        self._analyzer = SentimentIntensityAnalyzer()
        self._analyzer.lexicon.update(_FINANCE_LEXICON)

    def score(self, text: str) -> float:
        return float(self._analyzer.polarity_scores(text)["compound"])


class FinBertAnalyzer(SentimentAnalyzer):
    """FinBERT sentiment (optional upgrade; needs the ``finbert`` extra)."""

    name = "finbert"

    def __init__(self) -> None:
        from transformers import pipeline

        self._pipe = pipeline("sentiment-analysis", model="ProsusAI/finbert")

    def score(self, text: str) -> float:
        result = self._pipe(text[:512])[0]
        label = result["label"].lower()
        confidence = float(result["score"])
        if label == "positive":
            return confidence
        if label == "negative":
            return -confidence
        return 0.0


def get_news_provider(settings: Settings) -> NewsProvider:
    if settings.finnhub_api_key:
        return FinnhubNewsProvider(settings.finnhub_api_key)
    return YFinanceNewsProvider()


def get_sentiment_analyzer(settings: Settings) -> SentimentAnalyzer:
    if settings.sentiment_backend == "finbert":
        try:
            return FinBertAnalyzer()
        except Exception:  # noqa: BLE001 — transformers/torch not installed; fall back
            pass
    return VaderAnalyzer()


def _label(score: float) -> str:
    if score > _POS_LABEL:
        return "positive"
    if score < _NEG_LABEL:
        return "negative"
    return "neutral"


def analyze_ticker(
    ticker: str,
    news_provider: NewsProvider,
    analyzer: SentimentAnalyzer,
    lookback_days: int = 7,
    max_items: int = 12,
) -> SentimentReport:
    """Fetch recent headlines for ``ticker`` and return an aggregate sentiment report."""
    items = news_provider.get_news(ticker.upper(), lookback_days)[:max_items]
    for item in items:
        item.sentiment = analyzer.score(item.headline)

    scored = [i.sentiment for i in items if i.sentiment is not None]
    mean_score = sum(scored) / len(scored) if scored else 0.0
    return SentimentReport(
        ticker=ticker.upper(),
        score=mean_score,
        label=_label(mean_score),
        count=len(items),
        backend=analyzer.name,
        items=items,
    )
