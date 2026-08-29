"""Unit tests for SkewStore persistence and queries using in-memory DuckDB."""

from __future__ import annotations

import datetime as dt
import math

from stock_monitor.skew_engine import SectorSummary, SkewRecord
from stock_monitor.skew_store import SkewStore
from stock_monitor.storage.db import Storage


def _make_sample_record(ticker: str, sector: str, norm_skew: float, ret_1m: float) -> SkewRecord:
    from stock_monitor.skew_math import classify_quadrant

    quad = classify_quadrant(ret_1m, norm_skew)
    return SkewRecord(
        ticker=ticker,
        sector=sector,
        spot=150.0,
        ret_1m=ret_1m,
        rel_ret_spy=ret_1m - 0.02,
        rvol=1.2,
        expiration="2025-05-16",
        dte_days=45,
        atm_iv=0.25,
        call_25d_iv=0.25 - (norm_skew * 0.25) / 2,
        put_25d_iv=0.25 + (norm_skew * 0.25) / 2,
        raw_skew=norm_skew * 0.25,
        normalized_skew=norm_skew,
        quadrant=quad,
        earnings_date="2025-06-01",
        is_earnings_near=False,
        sanity_passed=True,
        sanity_warning=None,
        sector_avg_raw_skew=0.03,
        sector_avg_norm_skew=0.12,
        sector_agreement=0.85,
        verdict=f"{ticker} verdict",
    )


def test_skew_store_save_and_query_snapshot() -> None:
    with Storage(":memory:") as db:
        store = SkewStore(db)

        d1 = dt.date(2025, 4, 1)
        r1 = _make_sample_record("AAPL", "Technology", -0.15, -0.05)  # Contrarian Bid
        r2 = _make_sample_record("MSFT", "Technology", 0.20, 0.08)   # Hedged Rally

        s_tech = SectorSummary(
            sector="Technology",
            ticker_count=2,
            avg_raw_skew=0.01,
            avg_norm_skew=0.025,
            avg_ret_1m=0.015,
            agreement=0.5,
            dominant_lean="Neutral",
        )

        store.save_snapshot(d1, [r1, r2], {"Technology": s_tech})

        assert store.get_latest_date() == d1

        records = store.get_snapshot_records(d1)
        assert len(records) == 2
        tickers = {r["ticker"] for r in records}
        assert tickers == {"AAPL", "MSFT"}

        # Filter by quadrant
        cb_records = store.get_snapshot_records(d1, quadrant="Contrarian Bid")
        assert len(cb_records) == 1
        assert cb_records[0]["ticker"] == "AAPL"

        # Sector summaries
        sectors = store.get_snapshot_sectors(d1)
        assert len(sectors) == 1
        assert sectors[0]["sector"] == "Technology"
        assert sectors[0]["ticker_count"] == 2


def test_skew_store_idempotent_reinsert() -> None:
    with Storage(":memory:") as db:
        store = SkewStore(db)

        d1 = dt.date(2025, 4, 1)
        r1 = _make_sample_record("AAPL", "Technology", -0.15, -0.05)
        store.save_snapshot(d1, [r1], {})
        assert len(store.get_snapshot_records(d1)) == 1

        # Re-save with updated metric
        r1_updated = _make_sample_record("AAPL", "Technology", -0.25, -0.07)
        store.save_snapshot(d1, [r1_updated], {})
        records = store.get_snapshot_records(d1)
        assert len(records) == 1
        assert records[0]["normalized_skew"] == -0.25


def test_skew_store_changes_and_trends() -> None:
    with Storage(":memory:") as db:
        store = SkewStore(db)

        d1 = dt.date(2025, 4, 1)
        d2 = dt.date(2025, 4, 8)

        # Day 1: AAPL in Fear (down + puts bid)
        r_d1 = _make_sample_record("AAPL", "Technology", 0.20, -0.04)
        store.save_snapshot(d1, [r_d1], {})

        # Day 2: AAPL flips to Contrarian Bid (down + calls bid)
        r_d2 = _make_sample_record("AAPL", "Technology", -0.15, -0.06)
        store.save_snapshot(d2, [r_d2], {})

        # Test trend
        trend = store.get_ticker_trend("AAPL")
        assert len(trend) == 2
        d_val_0 = trend[0]["snapshot_date"]
        date_0 = (
            d_val_0.date()
            if hasattr(d_val_0, "date")
            else dt.date.fromisoformat(str(d_val_0)[:10])
        )
        assert date_0 == d1
        d_val_1 = trend[1]["snapshot_date"]
        date_1 = (
            d_val_1.date()
            if hasattr(d_val_1, "date")
            else dt.date.fromisoformat(str(d_val_1)[:10])
        )
        assert date_1 == d2

        # Test changes / WoW delta
        changes = store.get_skew_changes(as_of=d2, lookback_days=7)
        assert len(changes) == 1
        ch = changes[0]
        assert ch["ticker"] == "AAPL"
        assert ch["prev_quadrant"] == "Fear"
        assert ch["current_quadrant"] == "Contrarian Bid"
        assert ch["quadrant_changed"] is True
        assert math.isclose(ch["skew_change_norm"], -0.35, abs_tol=1e-4)
