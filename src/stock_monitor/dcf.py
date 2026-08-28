"""DCF intrinsic value — a simple, honest reverse-check on the market's price.

The model: a two-stage free-cash-flow DCF off PIT-correct SEC facts.

- Stage 1 (5 explicit years): FCF grows at ``growth`` — the user-chosen anchor
  (revenue CAGR by default), clamped to a sane band.
- Stage 2: terminal value via Gordon growth at ``terminal_growth`` (default 2.5%,
  below long-run nominal GDP) discounted at WACC (default 8.5%).

The DCF is only as good as its inputs, so every result ships with the inputs and a
confidence grade:
- ``good``    — OCF, capex, shares and a price all present (equity/debt optional).
- ``rough``   — capex missing (FCF proxied by OCF) or net debt unknown.
- ``none``    — not computable (no cash-flow history); reasons are listed.

Deliberately NOT here: analyst forecasts, P/Es, or any LLM. Numbers come from
filings; the user's assumptions come from the query string.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from typing import Any

from stock_monitor.features.builder import latest_fact
from stock_monitor.providers.base import FundamentalFact

_EXPLICIT_YEARS = 5
# Clamp the growth anchor so a single great (or disastrous) year can't pretend the
# company compounds at ±40% forever.
_MIN_GROWTH = -0.10
_MAX_GROWTH = 0.30
_DEFAULT_WACC = 0.085
_DEFAULT_TERMINAL_GROWTH = 0.025
# Facts older than this are labelled stale in the response (seasonality can explain
# part of the gap for quarterly facts, so this is a soft label, not a rejection).
_STALE_AFTER_DAYS = 550

_CONCEPTS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "CommonStockSharesOutstanding",
    "StockholdersEquity",
    "Liabilities",
    "CashAndCashEquivalentsAtCarryingValue",
    "LongTermDebtNoncurrent",
    "LongTermDebtCurrent",
    "ShortTermBorrowings",
)

# (fiscal year, value) pairs, oldest first.
YearSeries = list[tuple[int, float]]


def dcf_concepts() -> tuple[str, ...]:
    """Concepts the DCF engine needs (EDGAR companyfacts already carries them)."""
    return _CONCEPTS


def _annual_series(
    facts: Sequence[FundamentalFact], concept: str, as_of: dt.date
) -> YearSeries:
    """Latest-known value per fiscal year for ``concept``, oldest first (PIT-safe).

    Only annual facts qualify (period >= 300 days ≈ a 10-K) so quarterly noise
    never fakes a trend.
    """
    best: dict[int, FundamentalFact] = {}
    for fact in facts:
        if fact.concept != concept or fact.known_on > as_of:
            continue
        jan1 = fact.fiscal_end.replace(month=1, day=1)
        if (fact.fiscal_end - jan1).days < 300:
            continue
        year = fact.fiscal_end.year
        prior = best.get(year)
        if prior is None or (fact.fiscal_end, fact.known_on) > (
            prior.fiscal_end, prior.known_on
        ):
            best[year] = fact
    return sorted((int(year), float(fact.value)) for year, fact in best.items())


def _cagr(series: YearSeries) -> float | None:
    """CAGR of the series; needs 2+ positive points within the explicit window."""
    recent = [(y, v) for y, v in series if v > 0][-_EXPLICIT_YEARS:]
    if len(recent) < 2:
        return None
    (y0, v0), (y1, v1) = recent[0], recent[-1]
    years = max(float(y1 - y0), 0.5)
    growth = (v1 / v0) ** (1.0 / years) - 1.0
    return growth if math.isfinite(growth) else None


def _slope_growth(series: YearSeries) -> float | None:
    """Fallback anchor: least-squares growth from losses or near-flat spans."""
    recent = series[-_EXPLICIT_YEARS:]
    if len(recent) < 2:
        return None
    xs = [float(y) for y, _ in recent]
    ys = [float(v) for _, v in recent]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum(
        (x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)
    ) / denom
    if mean_y == 0 or not math.isfinite(slope):
        return None
    return slope / abs(mean_y)


def compute_dcf(
    facts: Sequence[FundamentalFact],
    price: float | None,
    *,
    as_of: dt.date | None = None,
    growth: float | None = None,
    wacc: float | None = None,
    terminal_growth: float | None = None,
) -> dict[str, Any]:
    """Two-stage FCF DCF over PIT facts. Never raises — returns a graded result."""
    as_of = as_of or dt.date.today()
    wacc_v = _DEFAULT_WACC if wacc is None else float(wacc)
    term_v = _DEFAULT_TERMINAL_GROWTH if terminal_growth is None else float(terminal_growth)
    reasons: list[str] = []

    if not 0.0 < wacc_v < 0.5:
        return _none_result(["wacc must be between 0 and 50%"])
    if term_v < 0 or term_v >= wacc_v:
        return _none_result(["terminal growth must be >= 0 and below wacc"])

    ocf_series = _annual_series(facts, "NetCashProvidedByUsedInOperatingActivities", as_of)
    if not ocf_series:
        return _none_result(["no operating cash flow history in SEC filings"])

    capex_by_year = dict(
        _annual_series(facts, "PaymentsToAcquirePropertyPlantAndEquipment", as_of)
    )
    fcf_series: YearSeries = []
    for year, ocf in ocf_series:
        capex = capex_by_year.get(year)
        fcf_series.append((year, ocf - capex if capex is not None else ocf))
    capex_missing = all(year not in capex_by_year for year, _ in ocf_series)
    if capex_missing:
        reasons.append("capex not reported — FCF proxied by operating cash flow")

    base_year, base_fcf = fcf_series[-1]

    # ---- Growth anchor -------------------------------------------------------
    revenue = _annual_series(facts, "Revenues", as_of) or _annual_series(
        facts, "RevenueFromContractWithCustomerExcludingAssessedTax", as_of
    )
    fcf_negative = base_fcf <= 0
    revenue_growth = _cagr(revenue)
    if growth is not None:
        growth_source = "manual"
        effective_growth: float | None = float(growth)
    elif revenue_growth is not None:
        growth_source = "revenue cagr"
        effective_growth = revenue_growth
    else:
        growth_source = "least-squares cash-flow trend"
        effective_growth = _slope_growth(fcf_series)
        if effective_growth is None and fcf_negative:
            reasons.append("cash flows are negative with no growth anchor — no DCF possible")
    effective_growth = effective_growth if effective_growth is not None else 0.0
    growth_v = max(_MIN_GROWTH, min(_MAX_GROWTH, effective_growth))
    if effective_growth != growth_v:
        reasons.append(
            f"growth {effective_growth:.1%} clamped to {growth_v:.1%} (sane band "
            f"{_MIN_GROWTH:.0%}..{_MAX_GROWTH:.0%})"
        )

    if fcf_negative and growth_v <= 0:
        reasons.append(
            "latest FCF is negative and no positive growth anchor — "
            "a DCF here would be fiction"
        )
        return _none_result(reasons, base_fcf=base_fcf)

    # ---- PV of the explicit stage -------------------------------------------
    pv_explicit = 0.0
    flows: list[dict[str, float]] = []
    fcf = base_fcf
    for offset in range(1, _EXPLICIT_YEARS + 1):
        fcf = fcf * (1.0 + growth_v)
        pv = fcf / (1.0 + wacc_v) ** offset
        pv_explicit += pv
        flows.append({"year": float(base_year + offset), "fcf": fcf, "pv": pv})

    # ---- Terminal value (Gordon) --------------------------------------------
    terminal_fcf = fcf * (1.0 + term_v)
    terminal_value = terminal_fcf / (wacc_v - term_v)
    pv_terminal = terminal_value / (1.0 + wacc_v) ** _EXPLICIT_YEARS
    ev = pv_explicit + pv_terminal

    # ---- Equity bridge -------------------------------------------------------
    shares_fact = latest_fact(facts, "CommonStockSharesOutstanding", as_of)
    shares = shares_fact.value if shares_fact else None
    if not shares or shares_fact is None:
        reasons.append("no share count in SEC filings")
        return _none_result(reasons, base_fcf=base_fcf)

    equity_fact = latest_fact(facts, "StockholdersEquity", as_of)
    liabilities_fact = latest_fact(facts, "Liabilities", as_of)
    cash_series = _annual_series(facts, "CashAndCashEquivalentsAtCarryingValue", as_of)
    debt_by_year: dict[int, float] = {}
    for concept in ("LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings"):
        for year, value in _annual_series(facts, concept, as_of):
            debt_by_year[year] = debt_by_year.get(year, 0.0) + value
    cash = cash_series[-1][1] if cash_series else 0.0

    net_debt: float | None = None
    bridge: str
    if equity_fact is not None and liabilities_fact is not None:
        net_debt = liabilities_fact.value - cash
        bridge = "liabilities − cash"
    elif debt_by_year:
        net_debt = debt_by_year[max(debt_by_year)] - cash
        bridge = "filed debt − cash"
    else:
        bridge = "unavailable — treated as zero"

    equity_value = ev - net_debt if net_debt is not None else ev
    per_share = equity_value / shares
    upside = (per_share / price - 1.0) if price else None

    confidence = "good"
    if capex_missing or net_debt is None:
        confidence = "rough"

    age_days = (as_of - dt.date(base_year, 12, 31)).days
    inputs = {
        "base_fcf": base_fcf,
        "fcf_years": f"{fcf_series[0][0]}–{fcf_series[-1][0]}",
        "growth_pct": growth_v,
        "growth_source": growth_source,
        "wacc_pct": wacc_v,
        "terminal_growth_pct": term_v,
        "shares": shares,
        "shares_known_on": shares_fact.known_on.isoformat(),
        "net_debt": net_debt,
        "bridge": bridge,
        "cash_known_on": (
            f"{cash_series[-1][0]}-12-31" if cash_series and net_debt is not None else None
        ),
        "price": price,
        "fundamentals_age_days": age_days,
    }
    if age_days > _STALE_AFTER_DAYS:
        reasons.append(
            f"filings are {age_days // 30} months old — recent quarters may change the picture"
        )

    if upside is None:
        verdict: str | None = None
    elif upside >= 0.15:
        verdict = "undervalued"
    elif upside <= -0.15:
        verdict = "overvalued"
    else:
        verdict = "fairly valued"

    return {
        "value": per_share,
        "upside_pct": upside,
        "confidence": confidence,
        "reasons": reasons,
        "inputs": inputs,
        "pv_explicit": pv_explicit,
        "pv_terminal": pv_terminal,
        "terminal_weight": pv_terminal / ev if ev else None,
        "flows": flows,
        "verdict": verdict,
    }


def _none_result(reasons: list[str], *, base_fcf: float | None = None) -> dict[str, Any]:
    return {
        "value": None,
        "upside_pct": None,
        "confidence": "none",
        "reasons": reasons,
        "inputs": {
            "base_fcf": base_fcf,
            "fcf_years": None,
            "growth_pct": None,
            "growth_source": None,
            "wacc_pct": None,
            "terminal_growth_pct": None,
            "shares": None,
            "shares_known_on": None,
            "net_debt": None,
            "bridge": None,
            "cash_known_on": None,
            "price": None,
            "fundamentals_age_days": None,
        },
        "pv_explicit": None,
        "pv_terminal": None,
        "terminal_weight": None,
        "flows": [],
        "verdict": None,
    }
