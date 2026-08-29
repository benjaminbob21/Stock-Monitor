"""Report generator and CSV exporter for Options Skew Map.

Outputs:
1. Console overview with quadrant breakdown, sector table, and Part 5 verdict sentences.
2. CSV export matching the canonical Skew Map spreadsheet columns.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from stock_monitor.skew_engine import SectorSummary, SkewRecord

logger = logging.getLogger(__name__)


def export_skew_to_csv(
    records: list[SkewRecord],
    output_path: Path | str,
) -> Path:
    """Export Skew records to a CSV file matching the canonical sheet format."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for r in records:
        rows.append(
            {
                "Ticker": r.ticker,
                "Sector": r.sector,
                "Spot Price": round(r.spot, 2),
                "1M Return": f"{r.ret_1m:.2%}",
                "Rel Return vs SPY": f"{r.rel_ret_spy:+.2%}",
                "RVOL": round(r.rvol, 2),
                "Expiry": r.expiration,
                "DTE": r.dte_days,
                "ATM IV": f"{r.atm_iv:.2%}",
                "25D Call IV": f"{r.call_25d_iv:.2%}",
                "25D Put IV": f"{r.put_25d_iv:.2%}",
                "Raw Skew (Pts)": round(r.raw_skew * 100.0, 2),
                "Norm Skew": round(r.normalized_skew, 3),
                "Quadrant": r.quadrant,
                "Sector Avg Norm Skew": round(r.sector_avg_norm_skew, 3),
                "Sector Agreement": f"{r.sector_agreement:.0%}",
                "Earnings Near": "YES" if r.is_earnings_near else "NO",
                "Earnings Date": r.earnings_date or "",
                "Sanity Passed": "YES" if r.sanity_passed else "NO",
                "Sanity Warning": r.sanity_warning or "",
                "Verdict Sentence": r.verdict,
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    logger.info("Saved skew report to %s (%d rows)", path, len(df))
    return path


def format_console_report(
    snapshot_date: dt.date,
    records: list[SkewRecord],
    sector_summaries: dict[str, SectorSummary],
) -> str:
    """Generate a clean, readable text report of the Skew Map."""
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"  OPTIONS SKEW MAP REPORT — {snapshot_date.isoformat()}")
    lines.append("  Framework: 'Build Your Own Skew Map' (berttrading)")
    lines.append("=" * 80)

    # 1. Quadrant summary counts
    quad_counts = {
        "Contrarian Bid": 0,
        "Chase": 0,
        "Hedged Rally": 0,
        "Fear": 0,
    }
    for r in records:
        quad_counts[r.quadrant] = quad_counts.get(r.quadrant, 0) + 1

    lines.append("\n📊 QUADRANT DISTRIBUTION:")
    lines.append(
        f"  • Contrarian Bid (Down + Calls Bid) : {quad_counts['Contrarian Bid']:2d}  "
        "[Primary Watchlist]"
    )
    lines.append(
        f"  • Chase          (Up   + Calls Bid) : {quad_counts['Chase']:2d}  "
        "[Crowded / Exhaustion]"
    )
    lines.append(
        f"  • Hedged Rally   (Up   + Puts Bid)  : {quad_counts['Hedged Rally']:2d}  "
        "[Institutional Trend]"
    )
    lines.append(
        f"  • Fear           (Down + Puts Bid)  : {quad_counts['Fear']:2d}  "
        "[Capitulation / Avoid]"
    )

    # 2. Sector Overview & Agreement table
    lines.append("\n🌐 SECTOR SUMMARY & AGREEMENT (Trap #2 & #3):")
    hdr = (
        f"  {'Sector':<24} {'Count':<6} {'Raw Skew':<10} "
        f"{'Norm Skew':<11} {'Lean':<10} {'Agreement':<10}"
    )
    lines.append(hdr)
    lines.append("  " + "-" * 75)
    for s in sorted(sector_summaries.values(), key=lambda x: x.sector):
        lines.append(
            f"  {s.sector:<24} {s.ticker_count:<6d} {s.avg_raw_skew*100:>7.2f} pts "
            f"{s.avg_norm_skew:>+10.2f}  {s.dominant_lean:<10} {s.agreement:>8.0%}"
        )

    # 3. Contrarian Bid watchlist (Top names to investigate)
    contrarian = [r for r in records if r.quadrant == "Contrarian Bid"]
    lines.append("\n🎯 CONTRARIAN BID CANDIDATES (Down 1M + Calls Bid):")
    if not contrarian:
        lines.append("  (No tickers currently in Contrarian Bid quadrant)")
    else:
        # Sort by most negative normalized skew (highest call demand)
        contrarian.sort(key=lambda r: r.normalized_skew)
        for r in contrarian:
            lines.append(f"  ▶ {r.verdict}")

    # 4. Hedged Rallies
    hedged = [r for r in records if r.quadrant == "Hedged Rally"]
    if hedged:
        lines.append("\n🛡️ HEDGED RALLIES (Trending Up + Protection Active — Tighten Stops):")
        hedged.sort(key=lambda r: r.ret_1m, reverse=True)
        for r in hedged[:5]:
            lines.append(f"  ▶ {r.verdict}")

    # 5. Chasing names
    chase = [r for r in records if r.quadrant == "Chase"]
    if chase:
        lines.append("\n⚠️ CHASE / EUPHORIA (Extended Up + Calls Bid — High Reversal Risk):")
        chase.sort(key=lambda r: r.normalized_skew)
        for r in chase[:5]:
            lines.append(f"  ▶ {r.verdict}")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)
