"""Point-in-time news event contracts and normalization helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class EventRecord:
    """A news event available to a short-horizon decision at ``known_at``."""

    ticker: str
    headline: str
    source: str
    published_at: dt.datetime
    known_at: dt.datetime
    url: str = ""
    sentiment: float | None = None
    category: str = "other"
    importance: float = 0.0

    @property
    def event_id(self) -> str:
        """Stable deduplication key for the same source event."""
        raw = "|".join(
            (self.ticker.upper(), self.url or self.headline, self.source.lower())
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def as_utc(value: dt.datetime) -> dt.datetime:
    """Return an aware UTC timestamp; naive provider timestamps are treated as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def normalize_url(url: str) -> str:
    """Remove tracking fragments/query strings while retaining the canonical URL."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def normalize_event(item: EventRecord) -> EventRecord:
    """Normalize provider output before persistence or feature construction."""
    headline = re.sub(r"\s+", " ", item.headline).strip()
    return EventRecord(
        ticker=item.ticker.upper().strip(),
        headline=headline,
        source=item.source.strip(),
        published_at=as_utc(item.published_at),
        known_at=as_utc(item.known_at),
        url=normalize_url(item.url),
        sentiment=item.sentiment,
        category=item.category,
        importance=max(0.0, min(1.0, item.importance)),
    )


def dedupe_events(events: list[EventRecord]) -> list[EventRecord]:
    """Keep the earliest-known copy of each event in stable chronological order."""
    chosen: dict[str, EventRecord] = {}
    for raw in events:
        event = normalize_event(raw)
        previous = chosen.get(event.event_id)
        if previous is None or event.known_at < previous.known_at:
            chosen[event.event_id] = event
    return sorted(chosen.values(), key=lambda event: (event.published_at, event.event_id))


def classify_headline(headline: str) -> tuple[str, float]:
    """Assign a conservative event category and importance from headline text."""
    text = headline.lower()
    patterns = {
        "earnings": (r"earnings|revenue|profit|eps|guidance", 0.75),
        "clinical_or_product": (r"fda|trial|approval|vaccine|drug|launch|product", 0.9),
        "corporate_action": (r"acquire|acquisition|merger|buyback|dividend|spinoff", 0.85),
        "legal_or_regulatory": (r"lawsuit|probe|investigation|recall|fine|regulator", 0.8),
        "analyst_change": (r"upgrade|downgrade|price target|analyst", 0.55),
    }
    for category, (pattern, importance) in patterns.items():
        if re.search(pattern, text):
            return category, importance
    return "other", 0.25
