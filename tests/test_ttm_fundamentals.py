"""TTM flow-construction tests — the mixed-period fundamentals fix.

The original bug: net income / revenue / OCF / capex were taken as whatever
single fact was freshest, mixing DEF 14A annuals (Mastercard) with 10-Q YTD
cumulatives. Ratios like profit_margin became nonsense (1.78 or 0.85 instead
of ~0.46). These tests pin the TTM construction and its fallbacks.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_monitor.features.builder import _ttm_flow, build_feature_row
from stock_monitor.providers.base import FundamentalFact


def _fact(
    concept: str,
    value: float,
    fiscal_end: str,
    known_on: str,
    form: str = "10-Q",
    period_start: str | None = None,
) -> FundamentalFact:
    return FundamentalFact(
        ticker="MA",
        concept=concept,
        value=value,
        unit="USD",
        fiscal_end=dt.date.fromisoformat(fiscal_end),
        known_on=dt.date.fromisoformat(known_on),
        form=form,
        period_start=dt.date.fromisoformat(period_start) if period_start else None,
    )


# The real Mastercard scenario (values in $B from EDGAR companyfacts):
# NetIncomeLoss only carries a DEF 14A PvP annual; quarterly income lives in
# ProfitLoss. H1'26 YTD and Q2'26 standalone share fiscal_end 2026-06-30.
MA_FACTS = [
    _fact("NetIncomeLoss", 14.968e9, "2025-12-31", "2026-04-27", "DEF 14A", "2025-01-01"),
    _fact("ProfitLoss", 14.968e9, "2025-12-31", "2026-02-11", "10-K", "2025-01-01"),
    _fact("ProfitLoss", 3.882e9, "2026-03-31", "2026-01-30", "10-Q", "2026-01-01"),
    _fact("ProfitLoss", 8.270e9, "2026-06-30", "2026-04-30", "10-Q", "2026-01-01"),
    _fact("ProfitLoss", 4.388e9, "2026-06-30", "2026-07-30", "10-Q", "2026-04-01"),
    _fact("ProfitLoss", 6.981e9, "2025-06-30", "2025-07-31", "10-Q", "2025-01-01"),
    _fact("Revenues", 32.791e9, "2025-12-31", "2026-02-11", "10-K", "2025-01-01"),
    _fact("Revenues", 15.383e9, "2025-06-30", "2025-07-31", "10-Q", "2025-01-01"),
    _fact("Revenues", 17.675e9, "2026-06-30", "2026-07-30", "10-Q", "2026-01-01"),
    _fact("Revenues", 9.277e9, "2026-06-30", "2026-07-30", "10-Q", "2026-04-01"),
]


def test_ttm_ni_uses_profitloss_and_def14a_excluded() -> None:
    value, known = _ttm_flow(MA_FACTS, "NetIncomeLoss", dt.date(2026, 8, 20))
    # DEF 14A alone (non-filing form, and only fact for this concept) must not
    # silently become "the" TTM NI when a filing-form filter can apply.
    assert value is None or known == dt.date(2026, 4, 27)


def test_ttm_profitloss_full_construction() -> None:
    value, known = _ttm_flow(MA_FACTS, "ProfitLoss", dt.date(2026, 8, 20))
    assert value is not None
    # TTM = FY2025 14.968 + H1'26 YTD 8.270 − H1'25 YTD 6.981 = 16.257
    assert abs(value - 16.257e9) < 1e5
    # Freshest input is the H1'26 10-Q filed 2026-04-30.
    assert known == dt.date(2026, 4, 30)


def test_ttm_revenue_prefers_ytd_over_same_end_quarter() -> None:
    value, _ = _ttm_flow(MA_FACTS, "Revenues", dt.date(2026, 8, 20))
    assert value is not None
    # TTM = 32.791 + 17.675 − 15.383 = 35.083 (NOT 32.791 + 9.277 − …)
    assert abs(value - 35.083e9) < 1e5


def test_ttm_pit_annual_only_when_prior_comparative_absent() -> None:
    # After the FY25 10-K (filed 2026-02-11), the freshest period is Q1'26
    # (known since 2026-01-30) but its prior-year Q1 comparative is absent →
    # annual-only fallback, never a mixed annual+Q1 sum.
    value, known = _ttm_flow(MA_FACTS, "ProfitLoss", dt.date(2026, 2, 20))
    assert value is not None
    assert abs(value - 14.968e9) < 1e5
    assert known == dt.date(2026, 2, 11)


def test_ttm_returns_none_when_only_stale_subannual_known() -> None:
    # Before the FY25 10-K filing (2026-02-11) and before Q1'26, the only
    # knowable ProfitLoss fact is the stale H1'25 YTD → nothing usable,
    # no fabrication.
    value, known = _ttm_flow(MA_FACTS, "ProfitLoss", dt.date(2026, 1, 20))
    assert value is None
    assert known is None


def test_trailing_four_quarters_young_listing() -> None:
    facts = [
        _fact("NetIncomeLoss", 1.0e9, "2025-03-31", "2025-05-01", "10-Q", "2025-01-01"),
        _fact("NetIncomeLoss", 1.2e9, "2025-06-30", "2025-08-01", "10-Q", "2025-04-01"),
        _fact("NetIncomeLoss", 1.1e9, "2025-09-30", "2025-11-01", "10-Q", "2025-07-01"),
        _fact("NetIncomeLoss", 1.3e9, "2025-12-31", "2026-02-01", "10-Q", "2025-10-01"),
    ]
    value, known = _ttm_flow(facts, "NetIncomeLoss", dt.date(2026, 3, 1))
    assert value is not None
    assert abs(value - 4.6e9) < 1e5
    assert known == dt.date(2026, 2, 1)


def _prices(days: int = 400, end: str = "2026-08-20") -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=days)
    return pd.DataFrame({"close": 100.0}, index=idx)


def test_feature_row_profit_margin_sane_for_mastercard_scenario() -> None:
    row = build_feature_row("MA", _prices(), MA_FACTS, dt.date(2026, 8, 20))
    assert row is not None
    pm = row["profit_margin"]
    assert isinstance(pm, float)
    # Was 1.78 (mixed periods); must now be ≈ 16.257 / 35.083 ≈ 0.463.
    assert 0.40 < pm < 0.52


def test_feature_row_no_pollution_from_def14a_annual_ni() -> None:
    # If ProfitLoss facts are absent entirely, NetIncomeLoss has only the DEF 14A
    # annual → concept yields nothing (no form-filtered filing fact exists) and
    # NI-based ratios must be NaN, not fabricated from the DEF 14A value.
    facts = [f for f in MA_FACTS if f.concept != "ProfitLoss"]
    row = build_feature_row("MA", _prices(), facts, dt.date(2026, 8, 20))
    assert row is not None
    assert pd.isna(row["roe"])
    assert pd.isna(row["earnings_yield"])
