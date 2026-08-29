"""Storage and query layer for Options Skew snapshots and trends.

Persists:
- ``skew_daily``: daily skew metrics per ticker x snapshot_date.
- ``skew_sector_daily``: daily sector averages and agreement metrics.

Enables:
- Latest snapshot retrieval with quadrant groupings.
- Historical trend queries per ticker ("the level is structural, the change is the signal").
- Day-over-day and Week-over-week skew change detection and quadrant shifts.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from stock_monitor.skew_engine import SectorSummary, SkewRecord
from stock_monitor.storage.db import Storage

logger = logging.getLogger(__name__)


class SkewStore:
    """Store and query options skew analytical snapshots."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._con = storage._con

    def save_snapshot(
        self,
        snapshot_date: dt.date,
        records: list[SkewRecord],
        sector_summaries: dict[str, SectorSummary],
    ) -> None:
        """Idempotently save a full daily skew snapshot."""
        # Delete existing snapshot for date if re-running
        self._con.execute(
            "DELETE FROM skew_daily WHERE snapshot_date = ?",
            [snapshot_date],
        )
        self._con.execute(
            "DELETE FROM skew_sector_daily WHERE snapshot_date = ?",
            [snapshot_date],
        )

        # Bulk insert ticker records
        for r in records:
            self._con.execute(
                """
                INSERT INTO skew_daily (
                    snapshot_date, ticker, sector, spot, ret_1m, rel_ret_spy, rvol,
                    expiration, dte_days, atm_iv, call_25d_iv, put_25d_iv,
                    raw_skew, normalized_skew, quadrant, earnings_date,
                    is_earnings_near, sanity_passed, sanity_warning,
                    sector_avg_raw_skew, sector_avg_norm_skew, sector_agreement,
                    verdict
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot_date,
                    r.ticker,
                    r.sector,
                    r.spot,
                    r.ret_1m,
                    r.rel_ret_spy,
                    r.rvol,
                    r.expiration,
                    r.dte_days,
                    r.atm_iv,
                    r.call_25d_iv,
                    r.put_25d_iv,
                    r.raw_skew,
                    r.normalized_skew,
                    r.quadrant,
                    r.earnings_date,
                    r.is_earnings_near,
                    r.sanity_passed,
                    r.sanity_warning,
                    r.sector_avg_raw_skew,
                    r.sector_avg_norm_skew,
                    r.sector_agreement,
                    r.verdict,
                ],
            )

        # Bulk insert sector summaries
        for s in sector_summaries.values():
            self._con.execute(
                """
                INSERT INTO skew_sector_daily (
                    snapshot_date, sector, ticker_count, avg_raw_skew,
                    avg_norm_skew, avg_ret_1m, agreement, dominant_lean
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot_date,
                    s.sector,
                    s.ticker_count,
                    s.avg_raw_skew,
                    s.avg_norm_skew,
                    s.avg_ret_1m,
                    s.agreement,
                    s.dominant_lean,
                ],
            )

    def get_latest_date(self) -> dt.date | None:
        """Return the most recent snapshot date in the database."""
        row = self._con.execute(
            "SELECT MAX(snapshot_date) FROM skew_daily"
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return row[0] if isinstance(row[0], dt.date) else dt.date.fromisoformat(str(row[0]))

    def get_snapshot_records(
        self,
        snapshot_date: dt.date | None = None,
        quadrant: str | None = None,
        sector: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query skew records for a given date (defaults to latest)."""
        target_date = snapshot_date or self.get_latest_date()
        if target_date is None:
            return []

        query = "SELECT * FROM skew_daily WHERE snapshot_date = ?"
        params: list[Any] = [target_date]

        if quadrant:
            query += " AND quadrant = ?"
            params.append(quadrant)
        if sector:
            query += " AND sector = ?"
            params.append(sector)

        query += " ORDER BY ticker ASC"
        df = self._con.execute(query, params).fetchdf()
        return df.to_dict(orient="records")

    def get_snapshot_sectors(
        self,
        snapshot_date: dt.date | None = None,
    ) -> list[dict[str, Any]]:
        """Query sector summaries for a given date (defaults to latest)."""
        target_date = snapshot_date or self.get_latest_date()
        if target_date is None:
            return []

        df = self._con.execute(
            "SELECT * FROM skew_sector_daily WHERE snapshot_date = ? ORDER BY sector ASC",
            [target_date],
        ).fetchdf()
        return df.to_dict(orient="records")

    def get_ticker_trend(
        self,
        ticker: str,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        """Get time series of skew metrics for a single ticker."""
        df = self._con.execute(
            """
            SELECT snapshot_date, spot, ret_1m, rel_ret_spy, atm_iv,
                   call_25d_iv, put_25d_iv, raw_skew, normalized_skew,
                   quadrant, is_earnings_near, sector_agreement, verdict
            FROM skew_daily
            WHERE ticker = ?
            ORDER BY snapshot_date ASC
            LIMIT ?
            """,
            [ticker.upper(), limit],
        ).fetchdf()
        return df.to_dict(orient="records")

    def get_skew_changes(
        self,
        as_of: dt.date | None = None,
        lookback_days: int = 7,
    ) -> list[dict[str, Any]]:
        """Compute the delta in skew metrics between current snapshot and a prior snapshot.

        PDF principle: 'The level is structural; the change is the signal.'
        """
        curr_date = as_of or self.get_latest_date()
        if curr_date is None:
            return []

        # Find prior snapshot date (closest to lookback target, fallback to immediately preceding)
        target_prior = curr_date - dt.timedelta(days=max(1, lookback_days))
        row_prev = self._con.execute(
            """
            SELECT DISTINCT snapshot_date
            FROM skew_daily
            WHERE snapshot_date <= ? AND snapshot_date < ?
            ORDER BY snapshot_date DESC
            LIMIT 1
            """,
            [target_prior, curr_date],
        ).fetchone()

        if row_prev is None or row_prev[0] is None:
            row_prev = self._con.execute(
                """
                SELECT DISTINCT snapshot_date
                FROM skew_daily
                WHERE snapshot_date < ?
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                [curr_date],
            ).fetchone()

        if row_prev is None or row_prev[0] is None:
            # No prior date available yet
            records = self.get_snapshot_records(curr_date)
            return [
                {
                    **r,
                    "prev_date": None,
                    "skew_change_raw": 0.0,
                    "skew_change_norm": 0.0,
                    "prev_quadrant": r["quadrant"],
                    "quadrant_changed": False,
                }
                for r in records
            ]

        prev_date = row_prev[0]

        df = self._con.execute(
            """
            SELECT
                c.ticker,
                c.sector,
                c.spot,
                c.ret_1m,
                c.normalized_skew AS current_norm_skew,
                p.normalized_skew AS prev_norm_skew,
                (c.normalized_skew - p.normalized_skew) AS skew_change_norm,
                c.raw_skew AS current_raw_skew,
                p.raw_skew AS prev_raw_skew,
                (c.raw_skew - p.raw_skew) AS skew_change_raw,
                c.quadrant AS current_quadrant,
                p.quadrant AS prev_quadrant,
                (c.quadrant != p.quadrant) AS quadrant_changed,
                c.sector_agreement,
                c.is_earnings_near,
                c.verdict
            FROM skew_daily c
            JOIN skew_daily p ON c.ticker = p.ticker AND p.snapshot_date = ?
            WHERE c.snapshot_date = ?
            ORDER BY ABS(c.normalized_skew - p.normalized_skew) DESC
            """,
            [prev_date, curr_date],
        ).fetchdf()

        return df.to_dict(orient="records")
