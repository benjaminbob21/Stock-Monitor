"""Options Skew Map Service.

Orchestrates daily options skew collection, analytical transformation,
DuckDB snapshot persistence, and CSV export.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from stock_monitor.config import Settings, get_settings
from stock_monitor.skew_engine import SectorSummary, SkewRecord, process_skew_universe
from stock_monitor.skew_fetcher import fetch_spy_1m_return, fetch_universe_chains
from stock_monitor.skew_report import export_skew_to_csv
from stock_monitor.skew_store import SkewStore
from stock_monitor.skew_universe import get_skew_universe
from stock_monitor.storage.db import Storage

logger = logging.getLogger(__name__)


class SkewService:
    """End-to-end service for the Options Skew Map pipeline."""

    def __init__(self, storage: Storage, settings: Settings | None = None) -> None:
        self.storage = storage
        self.store = SkewStore(storage)
        self.settings = settings or get_settings()

    def run(
        self,
        snapshot_date: dt.date | None = None,
        tier: str | None = None,
        force: bool = False,
        max_workers: int = 6,
        export_csv: bool = True,
    ) -> tuple[list[SkewRecord], dict[str, SectorSummary], Path | None]:
        """Execute the options skew scan for a snapshot date."""
        target_date = snapshot_date or dt.date.today()
        selected_tier = tier or self.settings.skew_universe_tier
        started_at = dt.datetime.now()

        # Check if already collected for today unless force=True
        if not force:
            existing = self.store.get_snapshot_records(target_date)
            if existing:
                logger.info(
                    "Skew snapshot already exists for %s (%d rows). Use force=True to re-run.",
                    target_date,
                    len(existing),
                )
                # Load sector summaries
                sec_records = self.store.get_snapshot_sectors(target_date)
                sec_map = {
                    s["sector"]: SectorSummary(
                        sector=s["sector"],
                        ticker_count=s["ticker_count"],
                        avg_raw_skew=s["avg_raw_skew"],
                        avg_norm_skew=s["avg_norm_skew"],
                        avg_ret_1m=s["avg_ret_1m"],
                        agreement=s["agreement"],
                        dominant_lean=s["dominant_lean"],
                    )
                    for s in sec_records
                }
                # Reconstruct SkewRecords from existing
                records = [
                    SkewRecord(
                        ticker=r["ticker"],
                        sector=r["sector"],
                        spot=r["spot"],
                        ret_1m=r["ret_1m"],
                        rel_ret_spy=r["rel_ret_spy"],
                        rvol=r["rvol"],
                        expiration=r["expiration"],
                        dte_days=r["dte_days"],
                        atm_iv=r["atm_iv"],
                        call_25d_iv=r["call_25d_iv"],
                        put_25d_iv=r["put_25d_iv"],
                        raw_skew=r["raw_skew"],
                        normalized_skew=r["normalized_skew"],
                        quadrant=r["quadrant"],
                        earnings_date=r["earnings_date"],
                        is_earnings_near=r["is_earnings_near"],
                        sanity_passed=r["sanity_passed"],
                        sanity_warning=r["sanity_warning"],
                        sector_avg_raw_skew=r["sector_avg_raw_skew"],
                        sector_avg_norm_skew=r["sector_avg_norm_skew"],
                        sector_agreement=r["sector_agreement"],
                        verdict=r["verdict"],
                        ret_1d=r.get("ret_1d") or 0.0,
                        ret_1w=r.get("ret_1w") or 0.0,
                        thin_chain=bool(r.get("thin_chain") or False),
                    )
                    for r in existing
                ]
                return records, sec_map, None

        logger.info("Starting Options Skew scan for %s (tier=%s)", target_date, selected_tier)

        try:
            # 1. Fetch benchmark SPY return
            spy_1m_ret = fetch_spy_1m_return(target_date)
            logger.info("SPY 1M Return benchmark: %.2f%%", spy_1m_ret * 100.0)

            # 2. Get universe tickers
            tickers = get_skew_universe(selected_tier)
            logger.info("Fetching options chains for %d tickers...", len(tickers))

            # 3. Fetch raw chains
            raw_chains = fetch_universe_chains(tickers, as_of=target_date, max_workers=max_workers)
            logger.info("Successfully fetched %d chains (out of %d)", len(raw_chains), len(tickers))

            # 4. Run skew analytical engine
            records, sector_summaries = process_skew_universe(raw_chains, spy_1m_ret=spy_1m_ret)
            logger.info(
                "Processed %d valid skew records across %d sectors",
                len(records),
                len(sector_summaries),
            )

            # 5. Save snapshot to DuckDB
            self.store.save_snapshot(target_date, records, sector_summaries)

            # 6. Export CSV if requested
            csv_path: Path | None = None
            if export_csv:
                csv_path = Path("data") / "skew_snapshots" / f"skew_{target_date.isoformat()}.csv"
                export_skew_to_csv(records, csv_path)

            finished_at = dt.datetime.now()
            self.storage.record_run(
                job="skew_scan",
                status="success",
                detail=f"Processed {len(records)} tickers ({len(sector_summaries)} sectors)",
                started_at=started_at,
                finished_at=finished_at,
            )

            return records, sector_summaries, csv_path

        except Exception as exc:
            finished_at = dt.datetime.now()
            self.storage.record_run(
                job="skew_scan",
                status="failed",
                detail=f"Error: {exc}",
                started_at=started_at,
                finished_at=finished_at,
            )
            logger.exception("Skew scan failed: %s", exc)
            raise
