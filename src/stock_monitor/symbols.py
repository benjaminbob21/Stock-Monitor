"""Ticker ↔ company-name directory (SEC ``company_tickers.json``).

A free, comprehensive map of every SEC-registered US ticker to its company name. Two
jobs, both user-facing: (1) show the full company name on the scoring card, and (2) let
search match by company name — so you can find a stock without knowing its symbol.

Loaded lazily and cached (the file is ~10k rows, a few hundred KB) via the same
requests-cache store the EDGAR provider uses, so it costs at most one download.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import requests_cache

from stock_monitor.config import get_settings

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# ETFs missing from the SEC registry (it lists only issuers with CIK filers — SPY and
# QQQ are there, most Vanguard/iShares funds are not). Merged into the directory so
# index funds are searchable and nameable everywhere: search, stock pages, and baskets.
# They are NOT in the scan universe — a basket leg needs a price, not a conviction score.
ETF_OVERLAY: dict[str, str] = {
    "VOO": "Vanguard S&P 500 ETF",
    "VTI": "Vanguard Total Stock Market ETF",
    "VXUS": "Vanguard Total International Stock ETF",
    "VOOQ": "Vanguard S&P 500 ETF (Neos)",
    "VGK": "Vanguard FTSE Europe ETF",
    "VPL": "Vanguard FTSE Pacific ETF",
    "VWO": "Vanguard FTSE Emerging Markets ETF",
    "VEA": "Vanguard FTSE Developed Markets ETF",
    "VUG": "Vanguard Growth ETF",
    "VTV": "Vanguard Value ETF",
    "VIG": "Vanguard Dividend Appreciation ETF",
    "VYM": "Vanguard High Dividend Yield ETF",
    "IEF": "iShares 7-10 Year Treasury Bond ETF",
    "TLT": "iShares 20+ Year Treasury Bond ETF",
    "GLD": "SPDR Gold Shares",
    "SCHD": "Schwab US Dividend Equity ETF",
}


@dataclass(frozen=True)
class SymbolMatch:
    """A search hit: a ticker and its company name."""

    ticker: str
    name: str


class SymbolDirectory:
    """Ticker → company-name lookups and name/ticker search over SEC's registry."""

    def __init__(self) -> None:
        settings = get_settings()
        self._session = requests_cache.CachedSession(
            cache_name=".cache/edgar",
            backend="sqlite",
            expire_after=settings.http_cache_ttl,
        )
        self._session.headers.update({"User-Agent": settings.sec_user_agent})
        self._by_ticker: dict[str, str] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        if self._by_ticker is None:
            with self._lock:
                if self._by_ticker is None:
                    try:
                        resp = self._session.get(_TICKERS_URL, timeout=30)
                        resp.raise_for_status()
                        self._by_ticker = {
                            str(row["ticker"]).upper(): str(row.get("title", "")).strip()
                            for row in resp.json().values()
                            if row.get("ticker")
                        }
                        # Overlay fills registry gaps (Vanguard/iShares ETFs) without
                        # shadowing real registry entries.
                        for etf, name in ETF_OVERLAY.items():
                            self._by_ticker.setdefault(etf, name)
                    except Exception:  # noqa: BLE001 — directory is best-effort
                        # A network hiccup must not break scoring/search; treat as empty
                        # (names simply won't show) and retry on the next call.
                        return {}
        return self._by_ticker

    def name_for(self, ticker: str) -> str | None:
        """Return the company name for ``ticker`` (or ``None`` if unknown)."""
        return self._load().get(ticker.upper()) or None

    def search(self, query: str, *, limit: int = 15) -> list[SymbolMatch]:
        """Match ``query`` against tickers and company names, best matches first.

        Ranking: exact ticker → ticker prefix → company-name match (names that *start*
        with the query rank above names that merely contain it). Empty query → no hits.
        """
        q = query.strip().upper()
        if not q:
            return []
        data = self._load()

        exact: list[tuple[str, str]] = []
        ticker_prefix: list[tuple[str, str]] = []
        name_hits: list[tuple[str, str]] = []
        for ticker, name in data.items():
            if ticker == q:
                exact.append((ticker, name))
            elif ticker.startswith(q):
                ticker_prefix.append((ticker, name))
            elif q in name.upper():
                name_hits.append((ticker, name))

        ticker_prefix.sort(key=lambda tn: tn[0])
        # Names beginning with the query first, then alphabetical.
        name_hits.sort(key=lambda tn: (not tn[1].upper().startswith(q), tn[1]))
        ordered = exact + ticker_prefix + name_hits
        return [SymbolMatch(ticker=t, name=n) for t, n in ordered[:limit]]
