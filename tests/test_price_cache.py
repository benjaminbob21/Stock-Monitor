"""Offline tests for the persistent price cache.

Uses a fake upstream provider that counts calls and records the ranges it was asked
for, so we can prove the two guarantees that make the cache safe under Tiingo's
free-tier rate limit:

1. Training reads (``fetch_missing=False``) never call upstream.
2. Fills/appends (``fetch_missing=True``) fetch only the missing head/tail gap.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_monitor.providers.base import PRICE_COLUMNS, PriceProvider
from stock_monitor.providers.price_cache import (
    CachedPriceProvider,
    PriceCache,
    refresh_price_cache,
)


class FakeUpstream(PriceProvider):
    """Deterministic business-day OHLCV generator that records the ranges it served."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dt.date, dt.date]] = []

    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        self.calls.append((ticker, start, end))
        idx = pd.bdate_range(start=start, end=end)
        if len(idx) == 0:
            return pd.DataFrame(columns=list(PRICE_COLUMNS))
        idx.name = "date"
        base = pd.Series(range(1, len(idx) + 1), index=idx, dtype="float64")
        return pd.DataFrame(
            {
                "open": base,
                "high": base + 1,
                "low": base - 1,
                "close": base + 0.5,
                "volume": base * 1000,
            }
        )


def test_upsert_read_coverage_roundtrip(tmp_path):
    cache = PriceCache(str(tmp_path / "prices.duckdb"))
    up = FakeUpstream()
    df = up.get_prices("AAPL", dt.date(2024, 1, 1), dt.date(2024, 1, 31))

    written = cache.upsert("AAPL", df)
    assert written == len(df)

    lo, hi = cache.coverage("AAPL")
    assert lo == df.index.min().date()
    assert hi == df.index.max().date()

    out = cache.read("AAPL", dt.date(2024, 1, 1), dt.date(2024, 1, 31))
    assert list(out.columns) == list(PRICE_COLUMNS)
    assert out.index.name == "date"
    assert len(out) == len(df)
    # Re-upsert is idempotent (no duplicate rows).
    cache.upsert("AAPL", df)
    assert len(cache.read("AAPL", dt.date(2024, 1, 1), dt.date(2024, 1, 31))) == len(df)


def test_coverage_none_when_empty(tmp_path):
    cache = PriceCache(str(tmp_path / "prices.duckdb"))
    assert cache.coverage("MSFT") is None
    assert cache.read("MSFT", dt.date(2024, 1, 1), dt.date(2024, 1, 5)).empty


def test_training_read_never_calls_upstream(tmp_path):
    cache = PriceCache(str(tmp_path / "prices.duckdb"))
    up = FakeUpstream()
    start, end = dt.date(2024, 1, 1), dt.date(2024, 3, 31)

    # Fill once (fetch_missing=True) then read as training would (fetch_missing=False).
    filler = CachedPriceProvider(up, cache, fetch_missing=True)
    filler.get_prices("NVDA", start, end)
    assert len(up.calls) == 1

    trainer = CachedPriceProvider(up, cache, fetch_missing=False)
    out = trainer.get_prices("NVDA", start, end)
    assert not out.empty
    # No new upstream calls: training is a pure cache read (can never 429).
    assert len(up.calls) == 1

    # A symbol absent from the cache returns empty for training (skipped gracefully).
    assert trainer.get_prices("TSLA", start, end).empty
    assert len(up.calls) == 1


def test_incremental_fetches_tail_only(tmp_path):
    cache = PriceCache(str(tmp_path / "prices.duckdb"))
    up = FakeUpstream()
    provider = CachedPriceProvider(up, cache, fetch_missing=True)

    provider.get_prices("KO", dt.date(2024, 1, 1), dt.date(2024, 1, 31))
    first_max = cache.coverage("KO")[1]
    up.calls.clear()

    # Extend the window forward: only the newer tail should be fetched.
    provider.get_prices("KO", dt.date(2024, 1, 1), dt.date(2024, 2, 29))
    assert len(up.calls) == 1
    _, tail_start, tail_end = up.calls[0]
    assert tail_start == first_max + dt.timedelta(days=1)
    assert tail_end == dt.date(2024, 2, 29)
    lo, hi = cache.coverage("KO")
    assert lo == dt.date(2024, 1, 1)
    assert hi >= dt.date(2024, 2, 28)


def test_refresh_price_cache_resumable(tmp_path):
    cache = PriceCache(str(tmp_path / "prices.duckdb"))
    up = FakeUpstream()
    end = dt.date(2024, 6, 30)

    first = refresh_price_cache(
        up, cache, ["SPY", "AAPL"], history_years=1, throttle_seconds=0, end=end
    )
    assert first["SPY"] > 0 and first["AAPL"] > 0
    assert len(up.calls) == 2

    up.calls.clear()
    # Second run over the same end date: nothing new to fetch, but no crash.
    second = refresh_price_cache(
        up, cache, ["SPY", "AAPL"], history_years=1, throttle_seconds=0, end=end
    )
    assert second["SPY"] == 0 and second["AAPL"] == 0
    # coverage unchanged; both names still present.
    assert set(cache.cached_tickers()) == {"SPY", "AAPL"}
