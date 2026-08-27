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
import re
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
    # Core analyst-stance words: absent from VADER's general lexicon entirely, so a
    # headline verdict like "But I'm Still Bearish" scored as pure noise while a
    # parenthetical "(Upgrade)" pulled it positive. Tiered with fraud/bankruptcy
    # because an explicit analyst stance is the headline's actual verdict.
    "bearish": -3.0, "bullish": 3.0,
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
    score: float  # recency+contrast-weighted mean sentiment in [-1, 1]
    label: str  # positive | neutral | negative | mixed
    count: int
    backend: str
    items: list[NewsItem] = field(default_factory=list)
    # True when there is meaningful disagreement across the scored headlines (a wide
    # positive<->negative spread). A single green/red dot is then misleading, so the
    # UI should render "mixed" rather than trust the aggregate sign.
    mixed: bool = False


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


# Neutrality on a contrast clause usually means "weak signal", but a single-pass
# classifier can also emit neutral while its classes fight (see ``torn_verdict``).


class SentimentAnalyzer(ABC):
    name: str

    @abstractmethod
    def score(self, text: str) -> float:
        """Return sentiment in [-1, 1]."""
        raise NotImplementedError

    def torn_verdict(self, text: str) -> bool:
        """True when the underlying classes disagree sharply (e.g. pos≈neg).

        Only probability-aware backends can answer; the default is "not torn" so
        scalar backends (VADER) keep the simple whole-text fallback behavior.
        """
        return False

    def score_batch(self, texts: list[str]) -> list[float]:
        """Score many texts at once. Default loops; backends may override for speed."""
        return [self.score(t) for t in texts]


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
        from typing import Any

        from transformers import pipeline

        self._pipe: Any = pipeline(
            "text-classification", model="ProsusAI/finbert"
        )



    @staticmethod
    def _to_probs(result: list[dict]) -> dict[str, float]:
        return {str(r["label"]).lower(): float(r["score"]) for r in result}

    def score(self, text: str) -> float:
        probs = self._to_probs(self._pipe(text[:512]))
        # Soft polarity: p(positive) - p(negative). Unlike a discrete argmax score,
        # this grades *contested* headlines (top label neutral, pos≈neg underneath)
        # as mildly signed instead of flattening them to 0.0 — which is what let
        # "But I'm Still Bearish (Upgrade)" escape with a positive/neutral verdict.
        return round(probs.get("positive", 0.0) - probs.get("negative", 0.0), 4)

    def score_batch(self, texts: list[str], *, batch_size: int = 64) -> list[float]:
        """Batched FinBERT inference — far faster than one call per headline on CPU."""
        if not texts:
            return []
        trimmed = [t[:512] for t in texts]
        out = self._pipe(trimmed, batch_size=batch_size, truncation=True)
        return [
            round(
                self._to_probs([item]).get("positive", 0.0)
                - self._to_probs([item]).get("negative", 0.0),
                4,
            )
            for item in out
        ]


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


# Conjunctions that flip or contrast the *final* verdict against an earlier clause.
# "Potential Merger Has Some Upside, But I'm Still Bearish" should read as bearish
# because the trailing clause is the author's actual stance. When one of these
# appears, we score the pre-contrast and post-contrast portions separately and give
# the *post* (concluding) portion the weight, because single-pass classifiers over-
# weight the leading positive tokens and miss the trailing verdict.
_CONTRAST_MARKERS = re.compile(
    r"\b(?:but|however|yet|although|though|despite|even so)\b", re.IGNORECASE
)

# How much the concluding (post-contrast) clause counts vs the leading clause.
# >0.5 lets the conclusion dominate; used only when a contrast marker is present.
_CONTRAST_POST_WEIGHT = 0.75


def _split_contrast(text: str) -> tuple[str, str] | None:
    """Split ``text`` at the first contrast conjunction into (leading, concluding).

    Returns ``None`` when there is no contrast marker, so callers keep the whole-text
    single-pass score in the common case.
    """
    match = _CONTRAST_MARKERS.search(text)
    if not match:
        return None
    tail = text[match.end() :]
    if not tail.strip():
        return None
    # Every contrast marker sits *between* the two clauses. Rebuild the leading part
    # from the start to the marker start, and the concluding part from marker end.
    return text[: match.start()].strip(), tail.strip()


# Neutrality on a contrast clause usually means "weak signal", but FinBERT can also
# return neutral when the tail tokens are ambiguous ("(Upgrade)" counters "Bearish").
# If the *whole* headline also reads neutral, prefer the concluding clause anyway —
# a single-pass model reading the full text is exactly what over-weights leading
# positives, so trusting it here would reproduce the bug this function fixes.
def score_with_contrast(analyzer: SentimentAnalyzer, text: str) -> float:
    """Score ``text`` with concluding-clause awareness.

    A single-pass classifier reads a headline as one unordered bag of words, so a
    trailing ``"But I'm Still Bearish"`` verdict gets drowned out by leading positives
    like "Upside"/"Upgrade". Here we detect a contrast marker, score each side
    independently, and let the *concluding* clause dominate.
    """
    parts = _split_contrast(text)
    if parts is None:
        return analyzer.score(text)
    lead_src, conclude_src = parts
    lead = analyzer.score(lead_src)
    conclude = analyzer.score(conclude_src)
    w = _CONTRAST_POST_WEIGHT
    # Neutral concluding clause usually means "no real signal in the tail" (a bare
    # trailing "yet") and the whole-text score should stand. But a single-pass model
    # can also report neutral when the tail's classes *fight* (FinBERT on the real
    # Tesla headline: tail pos .09 / neg .43 / neu .49 → top-label neutral) while it
    # still hands a confident-positive whole-text score by over-weighting the leading
    # clause — exactly the bug this function exists to fix. When the tail is torn
    # between positive and negative under the hood, trust the concluding clause.
    if _label(conclude) == "neutral":
        torn = getattr(analyzer, "torn_verdict", None)
        if callable(torn) and torn(conclude_src):
            return round(w * conclude + (1 - w) * lead, 4)
        return analyzer.score(text)
    return round(w * conclude + (1 - w) * lead, 4)


def _normalize_headline(text: str) -> str:
    """Lowercase + collapse whitespace for cheap near-duplicate detection."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def analyze_ticker(
    ticker: str,
    news_provider: NewsProvider,
    analyzer: SentimentAnalyzer,
    lookback_days: int = 7,
    max_items: int = 12,
    *,
    recency_weight: float = 0.6,
) -> SentimentReport:
    """Fetch recent headlines for ``ticker`` and return an aggregate sentiment report.

    Improves on the naive plain-mean aggregation in four ways:

    - **Contrast awareness**: ``score_with_contrast`` lets a trailing verdict
      ("But I'm Still Bearish") dominate a leading positive clause.
    - **Near-duplicate dedup**: the same story across sources is scored once.
    - **Recency weighting**: fresher headlines count more than older ones within the
      lookback window.
    - **Mixed-signal detection**: a wide positive↔negative spread yields label
      ``"mixed"`` so the UI doesn't paint one confident dot over disagreement.

    The aggregate ``score`` stays in ``[-1, 1]`` and the ``label`` remains one of
    ``positive|neutral|negative|mixed``, so existing consumers (news endpoint,
    positions/exit rules) keep working.
    """
    raw = news_provider.get_news(ticker.upper(), lookback_days)

    # 1) Deduplicate by normalized headline, keeping the most recent of a dup family.
    seen: dict[str, NewsItem] = {}
    for item in raw:
        key = _normalize_headline(item.headline)
        if not key:
            continue
        cur = seen.get(key)
        if cur is None or _newer(item.published, cur.published):
            seen[key] = item
    items = sorted(seen.values(), key=lambda i: i.published or dt.datetime.min, reverse=True)[
        :max_items
    ]

    # 2) Contrast-aware per-headline scoring.
    for item in items:
        item.sentiment = score_with_contrast(analyzer, item.headline)

    scored = [
        (i.sentiment, i.published) for i in items if i.sentiment is not None
    ]
    if not scored:
        return SentimentReport(
            ticker=ticker.upper(),
            score=0.0,
            label="neutral",
            count=0,
            backend=analyzer.name,
            items=items,
        )

    # 3) Recency-weighted mean: later-published items carry more weight.
    values = [s for s, _ in scored]
    pubs = [p for _, p in scored]
    recency_weights = [
        _recency_weight(p, pubs, recency_weight) for p in pubs
    ]
    wsum = sum(recency_weights) or 1.0
    mean_score = sum(s * w for s, w in zip(values, recency_weights, strict=False)) / wsum

    # 4) Mixed-signal detection: if strong positives and strong negatives both appear
    #    with meaningful counts, the aggregate sign is not trustworthy.
    mixed = _is_mixed(values)

    return SentimentReport(
        ticker=ticker.upper(),
        score=round(float(mean_score), 4),
        label=_label(float(mean_score)) if not mixed else "mixed",
        count=len(items),
        backend=analyzer.name,
        items=items,
        mixed=mixed,
    )


def _newer(a: dt.datetime | None, b: dt.datetime | None) -> bool:
    if a is None or b is None:
        return a is not None
    return a > b


def _recency_weight(
    published: dt.datetime | None,
    all_published: list[dt.datetime | None],
    weight: float,
) -> float:
    """Map a headline's age within the batch to a [weight, 1.0] multiplier.

    ``weight`` in [0, 1) is the attenuation applied to the oldest headline in the
    batch; the newest keeps weight 1.0. When timestamps are missing we assume the
    latest so nothing is unfairly punished.
    """
    known = [p for p in all_published if p is not None]
    if not known:
        return 1.0
    newest = max(known)
    if published is None:
        return 1.0
    span = (newest - min(known)).total_seconds() or 0.0
    if span <= 0:
        return 1.0
    age = (newest - published).total_seconds()
    frac = max(0.0, min(1.0, age / span))
    return round(1.0 - frac * (1.0 - weight), 4)


def _is_mixed(values: list[float]) -> bool:
    """True when the scored headlines disagree enough that aggregate sign misleads."""
    if not values:
        return False
    pos = [v for v in values if v > _POS_LABEL]
    neg = [v for v in values if v < _NEG_LABEL]
    if not pos or not neg:
        return False
    # Both camps are materially present, not a single outlier on either side.
    return len(pos) >= 1 and len(neg) >= 1 and len(pos) + len(neg) >= 2
