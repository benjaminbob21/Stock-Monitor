"""Earnings calendar (build-plan §7 Phase 3).

Knowing a name reports earnings in the next few days matters: the stock becomes a
coin-flip around the event, so we (a) cap its buy-side rank and (b) can warn on a
holding. Behind an interface like everything else — active only when a Finnhub key
is set, otherwise a no-op that returns "unknown".
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

import requests_cache

from stock_monitor.config import Settings

_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"


class EarningsProvider(ABC):
    name: str

    @abstractmethod
    def next_earnings_date(self, ticker: str) -> dt.date | None:
        """Return the next upcoming earnings date, or None if unknown."""
        raise NotImplementedError


class NullEarningsProvider(EarningsProvider):
    """No-op provider (used when no Finnhub key is configured)."""

    name = "none"

    def next_earnings_date(self, ticker: str) -> dt.date | None:
        return None


class FinnhubEarningsProvider(EarningsProvider):
    """Upcoming earnings dates from Finnhub's earnings calendar (free tier)."""

    name = "finnhub-earnings"

    def __init__(self, api_key: str, cache_ttl: int = 86_400) -> None:
        self._api_key = api_key
        self._session = requests_cache.CachedSession(
            cache_name=".cache/finnhub_earnings",
            backend="sqlite",
            expire_after=cache_ttl,
        )

    def next_earnings_date(self, ticker: str) -> dt.date | None:
        today = dt.date.today()
        horizon = today + dt.timedelta(days=90)
        try:
            resp = self._session.get(
                _EARNINGS_URL,
                params={
                    "from": today.isoformat(),
                    "to": horizon.isoformat(),
                    "symbol": ticker.upper(),
                    "token": self._api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
            calendar = resp.json().get("earningsCalendar", [])
        except Exception:  # noqa: BLE001 — earnings is optional; degrade to unknown
            return None

        upcoming = [
            dt.date.fromisoformat(e["date"])
            for e in calendar
            if e.get("date")
            and dt.date.fromisoformat(e["date"]) >= today
        ]
        return min(upcoming) if upcoming else None


def get_earnings_provider(settings: Settings) -> EarningsProvider:
    if settings.finnhub_api_key:
        return FinnhubEarningsProvider(settings.finnhub_api_key, settings.http_cache_ttl)
    return NullEarningsProvider()


def days_until_earnings(
    provider: EarningsProvider, ticker: str, today: dt.date | None = None
) -> int | None:
    """Return the number of days until the ticker's next earnings, or None."""
    date = provider.next_earnings_date(ticker)
    if date is None:
        return None
    return (date - (today or dt.date.today())).days
