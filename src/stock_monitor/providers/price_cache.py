"""Persistent price cache — the sustainable, high-quality training price path.

Tiingo's free tier caps at ~50 requests/hour, which makes a one-shot full-universe
retrain (30 years x ~48 names) brush the limit and 429. The fix is a **local cache**:

- Prices are stored once in a dedicated DuckDB file (``data/prices.duckdb``), keyed
  ``(ticker, date)`` and idempotent, so a fill can be re-run safely.
- **Training** reads *only* from the cache (``fetch_missing=False``) — zero Tiingo
  calls, so a retrain never hits the rate limit and always trains on 100% Tiingo data.
- A small **daily append** job (``fetch_missing=True``) pulls just the newest bars per
  ticker, so nothing is lost going forward without re-downloading decades of history.

The cache lives in its *own* DuckDB file (not the main ``stock_monitor.duckdb``) so that
filling/appending never needs the API service stopped and never contends with the main
single-writer lock. A process-level lock serialises in-process writers (scheduler jobs).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from pathlib import Path

import duckdb
import pandas as pd

from stock_monitor.providers.base import PRICE_COLUMNS, PriceProvider

logger = logging.getLogger("stock_monitor.price_cache")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker VARCHAR NOT NULL,
    date DATE NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    source VARCHAR,
    ingested_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (ticker, date)
);
"""


class PriceCache:
    """A DuckDB-backed store of split/dividend-adjusted OHLCV bars keyed (ticker, date)."""

    # Serialises writers *within* a process (e.g. two scheduler threads). Cross-process
    # writes are avoided by design: fills/appends run in one owner at a time.
    _lock = threading.Lock()

    def __init__(self, path: str = "data/prices.duckdb") -> None:
        self._path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Ensure the schema exists once up front.
        con = duckdb.connect(path)
        try:
            con.execute(_SCHEMA)
        finally:
            con.close()

    def coverage(self, ticker: str) -> tuple[dt.date, dt.date] | None:
        """Return the cached ``(min_date, max_date)`` for a ticker, or ``None`` if empty."""
        # No read_only here on purpose: DuckDB refuses to open the same file with a
        # different config than the read-write connections the API opens in one process,
        # so every connection must use the default (read-write) config.
        con = duckdb.connect(self._path)
        try:
            row = con.execute(
                "SELECT min(date), max(date) FROM prices WHERE ticker = ?",
                [ticker.upper()],
            ).fetchone()
        finally:
            con.close()
        if row is None or row[0] is None:
            return None
        return (row[0], row[1])

    def read(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """Return cached bars in ``[start, end]`` as the standard PriceProvider frame."""
        con = duckdb.connect(self._path)
        try:
            df = con.execute(
                """
                SELECT date, open, high, low, close, volume
                FROM prices
                WHERE ticker = ? AND date >= ? AND date <= ?
                ORDER BY date
                """,
                [ticker.upper(), start, end],
            ).df()
        finally:
            con.close()
        if df.empty:
            return pd.DataFrame(columns=list(PRICE_COLUMNS))
        df.index = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df.index.name = "date"
        return df.drop(columns=["date"])[list(PRICE_COLUMNS)]

    def upsert(self, ticker: str, prices: pd.DataFrame, source: str = "tiingo") -> int:
        """Insert-or-replace OHLCV bars for ``ticker``. Returns rows written."""
        if prices is None or prices.empty:
            return 0
        frame = prices.copy()
        frame = frame.reset_index().rename(columns={frame.index.name or "index": "date"})
        if "date" not in frame.columns:
            frame = frame.rename(columns={frame.columns[0]: "date"})
        frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.date
        frame["ticker"] = ticker.upper()
        frame["source"] = source
        cols = ["ticker", "date", *PRICE_COLUMNS, "source"]
        frame = frame.reindex(columns=cols)
        with self._lock:
            con = duckdb.connect(self._path)
            try:
                con.register("_prices_tmp", frame)
                con.execute(
                    """
                    INSERT INTO prices (ticker, date, open, high, low, close, volume, source)
                    SELECT ticker, date, open, high, low, close, volume, source
                    FROM _prices_tmp
                    ON CONFLICT (ticker, date) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        source = excluded.source
                    """
                )
            finally:
                con.unregister("_prices_tmp")
                con.close()
        return int(len(frame))

    def cached_tickers(self) -> list[str]:
        """Return the distinct tickers present in the cache."""
        con = duckdb.connect(self._path)
        try:
            rows = con.execute("SELECT DISTINCT ticker FROM prices ORDER BY ticker").fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]


class CachedPriceProvider(PriceProvider):
    """A :class:`PriceProvider` served from :class:`PriceCache`.

    - ``fetch_missing=False`` (training): read purely from the cache. Never calls the
      upstream provider, so a retrain makes **zero** Tiingo requests and can never 429.
      A ticker missing from the cache returns empty (training skips it gracefully).
    - ``fetch_missing=True`` (fill/append jobs): fetch only the *gap* bars (older head
      and/or newer tail) from upstream, persist them, then return the cached range.
    """

    name = "cached"

    def __init__(
        self, upstream: PriceProvider, cache: PriceCache, fetch_missing: bool = False
    ) -> None:
        self._upstream = upstream
        self._cache = cache
        self._fetch_missing = fetch_missing

    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        if self._fetch_missing:
            try:
                self._ensure(ticker, start, end)
            except Exception:  # noqa: BLE001 — a flaky upstream must not drop a cached name
                # Yahoo/Tiingo can rate-limit or error mid-scan. When that happens we
                # still have good history in the cache, so serve it instead of losing
                # the ticker entirely (this is what made scan counts fluctuate).
                logger.warning(
                    "price cache: upstream gap-fetch failed for %s; serving cached range",
                    ticker,
                    exc_info=True,
                )
        return self._cache.read(ticker, start, end)

    def get_quote(self, ticker: str) -> float | None:
        """Delegate live quotes straight to the upstream (never cached).

        Intraday prices must always be fresh, so this bypasses the daily-bar cache
        entirely. If the upstream can't provide a live quote it returns ``None`` and
        the caller falls back to the last completed close.
        """
        return self._upstream.get_quote(ticker)

    def _ensure(self, ticker: str, start: dt.date, end: dt.date) -> int:
        """Fetch and cache any bars in ``[start, end]`` not already stored. Returns rows added."""
        cov = self._cache.coverage(ticker)
        added = 0
        if cov is None:
            df = self._upstream.get_prices(ticker, start, end)
            added += self._cache.upsert(ticker, df, self._upstream.name)
            return added
        cmin, cmax = cov
        if start < cmin:
            head = self._upstream.get_prices(ticker, start, cmin - dt.timedelta(days=1))
            added += self._cache.upsert(ticker, head, self._upstream.name)
        if end > cmax:
            tail = self._upstream.get_prices(ticker, cmax + dt.timedelta(days=1), end)
            added += self._cache.upsert(ticker, tail, self._upstream.name)
        return added


def refresh_price_cache(
    upstream: PriceProvider,
    cache: PriceCache,
    tickers: list[str],
    *,
    history_years: int,
    throttle_seconds: float = 0.0,
    end: dt.date | None = None,
) -> dict[str, int]:
    """Fill/append the cache for ``tickers`` from ``upstream``, gap-only and resumable.

    ``throttle_seconds`` paces requests to stay under a provider's hourly cap (Tiingo
    free tier ~50/hr): at ~80s spacing, no rolling hour exceeds ~45 calls. Already-cached
    tickers only fetch the small recent tail, so re-runs are cheap. Returns rows added
    per ticker; a per-ticker failure (e.g. a 429) is logged and skipped so a partial fill
    still makes progress and can be resumed.
    """
    provider = CachedPriceProvider(upstream, cache, fetch_missing=True)
    end = end or dt.date.today()
    start = end - dt.timedelta(days=365 * history_years)
    added: dict[str, int] = {}
    for i, ticker in enumerate(tickers):
        try:
            added[ticker] = provider._ensure(ticker, start, end)
            logger.info("price cache: %s +%d rows", ticker, added[ticker])
        except Exception:  # noqa: BLE001 — one bad/rate-limited symbol must not abort the fill
            logger.exception("price cache fill failed for %s (skipped)", ticker)
            added[ticker] = 0
        if throttle_seconds and i < len(tickers) - 1:
            time.sleep(throttle_seconds)
    return added
