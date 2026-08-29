"""Options Skew Engine.

Transforms raw chain and market data into the full analytical Skew Map:
1. Computes ATM IV, 25d Call IV, 25d Put IV, Raw Skew, Normalized Skew.
2. Classifies names into the 4 Skew Map quadrants.
3. Computes Sector benchmarks & Sector Agreement (Trap #2 & Trap #3).
4. Generates the structured verdict sentence for every row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from stock_monitor.skew_fetcher import RawChainData
from stock_monitor.skew_math import (
    QuadrantType,
    SkewMetrics,
    compute_skew_metrics,
)
from stock_monitor.skew_universe import get_ticker_sector

logger = logging.getLogger(__name__)


@dataclass
class SkewRecord:
    ticker: str
    sector: str
    spot: float
    ret_1m: float
    rel_ret_spy: float
    rvol: float
    expiration: str
    dte_days: int
    atm_iv: float
    call_25d_iv: float
    put_25d_iv: float
    raw_skew: float  # Put IV - Call IV in vol points
    normalized_skew: float  # Raw Skew / ATM IV
    quadrant: QuadrantType
    earnings_date: str | None
    is_earnings_near: bool
    sanity_passed: bool
    sanity_warning: str | None
    sector_avg_raw_skew: float
    sector_avg_norm_skew: float
    sector_agreement: float  # Percentage of names in sector sharing same skew lean (0.0 to 1.0)
    verdict: str


@dataclass
class SectorSummary:
    sector: str
    ticker_count: int
    avg_raw_skew: float
    avg_norm_skew: float
    avg_ret_1m: float
    agreement: float  # 0.0 - 1.0
    dominant_lean: str  # "Calls Bid" or "Puts Bid"


def build_verdict_sentence(
    ticker: str,
    sector: str,
    ret_1m: float,
    rel_ret_spy: float,
    normalized_skew: float,
    sector_avg_norm_skew: float,
    sector_agreement: float,
    quadrant: QuadrantType,
    is_earnings_near: bool,
    earnings_date: str | None,
    sanity_passed: bool,
    sanity_warning: str | None,
) -> str:
    """Generate the fixed-format verdict sentence per Part 5 of the Skew Map method."""
    dir_str = "up" if ret_1m >= 0 else "down"
    ret_pct = abs(ret_1m) * 100.0

    if normalized_skew < 0:
        skew_desc = f"paying {abs(normalized_skew)*100.0:.1f}% more for upside calls"
    else:
        skew_desc = f"paying {normalized_skew*100.0:.1f}% more for downside puts"

    quad_actions = {
        "Contrarian Bid": (
            "Bullish divergence on pullback — prime candidate for reversal watchlist."
        ),
        "Chase": "Crowded upside euphoria — high risk of exhaustion, do not chase.",
        "Hedged Rally": "Uptrend with institutional hedging — hold long, tighten trailing stops.",
        "Fear": "Downtrend with elevated protection demand — avoid catching falling knives.",
    }

    action = quad_actions.get(quadrant, "")

    sentence = (
        f"{ticker} is {dir_str} {ret_pct:.1f}% over 30d (vs SPY {rel_ret_spy:+.1%}). "
        f"Options traders are {skew_desc} (norm skew {normalized_skew:+.2f} "
        f"vs {sector} avg {sector_avg_norm_skew:+.2f}). "
        f"{sector} shows {sector_agreement:.0%} agreement. "
        f"[{quadrant}]: {action}"
    )

    if is_earnings_near:
        sentence += f" [Warning: Event premium near earnings ({earnings_date})]"

    if not sanity_passed and sanity_warning:
        sentence += f" [Data warning: {sanity_warning}]"

    return sentence


def process_skew_universe(
    chains: list[RawChainData],
    spy_1m_ret: float = 0.0,
    r: float = 0.045,
    q: float = 0.0,
) -> tuple[list[SkewRecord], dict[str, SectorSummary]]:
    """Process a universe of raw options chains into skew records and sector statistics."""
    temp_records: list[tuple[RawChainData, SkewMetrics, str]] = []

    for chain in chains:
        if chain.error or chain.spot <= 0 or not chain.strikes:
            continue

        metrics = compute_skew_metrics(
            spot=chain.spot,
            strikes=chain.strikes,
            call_ivs=chain.call_ivs,
            put_ivs=chain.put_ivs,
            dte_days=chain.dte_days,
            ret_1m=chain.ret_1m,
            r=r,
            q=q,
        )

        if metrics is not None:
            sector = get_ticker_sector(chain.ticker)
            temp_records.append((chain, metrics, sector))

    # Calculate sector statistics
    # Sector agreement = % of names matching dominant skew sign (positive vs negative)
    sector_groups: dict[str, list[SkewMetrics]] = {}
    for _, m, sector in temp_records:
        sector_groups.setdefault(sector, []).append(m)

    sector_summaries: dict[str, SectorSummary] = {}
    for sector, m_list in sector_groups.items():
        count = len(m_list)
        avg_raw = sum(m.raw_skew for m in m_list) / count
        avg_norm = sum(m.normalized_skew for m in m_list) / count
        avg_ret = sum(m.ret_1m for m in m_list) / count

        # Check how many agree with the average sign
        if avg_norm < 0:
            dominant_lean = "Calls Bid"
            agreeing = sum(1 for m in m_list if m.normalized_skew < 0)
        else:
            dominant_lean = "Puts Bid"
            agreeing = sum(1 for m in m_list if m.normalized_skew >= 0)

        agreement = agreeing / count if count > 0 else 1.0

        sector_summaries[sector] = SectorSummary(
            sector=sector,
            ticker_count=count,
            avg_raw_skew=avg_raw,
            avg_norm_skew=avg_norm,
            avg_ret_1m=avg_ret,
            agreement=agreement,
            dominant_lean=dominant_lean,
        )

    # Build final SkewRecords
    final_records: list[SkewRecord] = []
    for chain, metrics, sector in temp_records:
        sec_sum = sector_summaries.get(
            sector,
            SectorSummary(
                sector=sector,
                ticker_count=1,
                avg_raw_skew=metrics.raw_skew,
                avg_norm_skew=metrics.normalized_skew,
                avg_ret_1m=metrics.ret_1m,
                agreement=1.0,
                dominant_lean="Calls Bid" if metrics.normalized_skew < 0 else "Puts Bid",
            ),
        )

        rel_ret_spy = metrics.ret_1m - spy_1m_ret

        verdict = build_verdict_sentence(
            ticker=chain.ticker,
            sector=sector,
            ret_1m=metrics.ret_1m,
            rel_ret_spy=rel_ret_spy,
            normalized_skew=metrics.normalized_skew,
            sector_avg_norm_skew=sec_sum.avg_norm_skew,
            sector_agreement=sec_sum.agreement,
            quadrant=metrics.quadrant,
            is_earnings_near=chain.is_earnings_near,
            earnings_date=chain.earnings_date,
            sanity_passed=metrics.sanity_passed,
            sanity_warning=metrics.sanity_warning,
        )

        record = SkewRecord(
            ticker=chain.ticker,
            sector=sector,
            spot=metrics.spot,
            ret_1m=metrics.ret_1m,
            rel_ret_spy=rel_ret_spy,
            rvol=chain.rvol,
            expiration=chain.expiration,
            dte_days=chain.dte_days,
            atm_iv=metrics.atm_iv,
            call_25d_iv=metrics.call_25d_iv,
            put_25d_iv=metrics.put_25d_iv,
            raw_skew=metrics.raw_skew,
            normalized_skew=metrics.normalized_skew,
            quadrant=metrics.quadrant,
            earnings_date=chain.earnings_date,
            is_earnings_near=chain.is_earnings_near,
            sanity_passed=metrics.sanity_passed,
            sanity_warning=metrics.sanity_warning,
            sector_avg_raw_skew=sec_sum.avg_raw_skew,
            sector_avg_norm_skew=sec_sum.avg_norm_skew,
            sector_agreement=sec_sum.agreement,
            verdict=verdict,
        )
        final_records.append(record)

    return final_records, sector_summaries
