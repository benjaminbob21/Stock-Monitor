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
# Terminal-value dominance warning: beyond this share of enterprise value the
# valuation is mostly a perpetuity bet, not a read on the business.
_TERMINAL_WEIGHT_WARN = 0.70
# Capex fallback for issuers that file no capex concept at all (mostly banks and
# insurers, whose cash-flow statements carry no PP&E purchases): capex estimated
# as this share of revenue, so FCF is haircut instead of silently proxied by OCF.
_CAPEX_TO_REVENUE_DEFAULT = 0.02

# Capex aliases, in preference order. Most industrials file
# PaymentsToAcquirePropertyPlantAndEquipment; some (BLBD, V, AIG) file the
# broader PaymentsToAcquireProductiveAssets; SPGI/PLTR split capex across
# several PaymentsToAcquire* tags that must be summed per year.
_CAPEX_PRIMARY = "PaymentsToAcquirePropertyPlantAndEquipment"
_CAPEX_ALIASES = (
    _CAPEX_PRIMARY,
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
    "PaymentsForCapitalExpenditures",
)
_CAPEX_SPLIT_CONCEPTS = (
    "PaymentsToAcquireOtherProductiveAssets",
    "PaymentsToAcquireInterestInJointVenture",
)

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
    """All concepts the DCF engine reads (mirrors edgar_provider.dcf_concepts)."""
    from stock_monitor.providers.edgar_provider import dcf_concepts as _edgar_dcf_concepts

    return _edgar_dcf_concepts()


def _annual_series(facts: Sequence[FundamentalFact], concept: str, as_of: dt.date) -> YearSeries:
    """Latest-known annual value per fiscal year for ``concept``, oldest first.

    PIT-safe: only facts with ``known_on <= as_of`` qualify. Annual facts are
    identified by ``form == 10-K`` when present; without form info the
    Jan-1-distance heuristic is the fallback, and among same-year facts the
    largest absolute value wins so a FY total beats its trailing sub-periods.
    """
    best: dict[int, FundamentalFact] = {}
    has_form = any(f.form for f in facts)
    for fact in facts:
        if fact.concept != concept or fact.known_on > as_of:
            continue
        if has_form:
            if fact.form != "10-K":
                continue
        else:
            jan1 = fact.fiscal_end.replace(month=1, day=1)
            if (fact.fiscal_end - jan1).days < 300:
                continue
        year = fact.fiscal_end.year
        prior = best.get(year)
        # A trailing ~9-month total is smaller in magnitude than the full FY,
        # so prefer the largest |value| when several facts share a fiscal year.
        if prior is None or abs(fact.value) >= abs(prior.value):
            best[year] = fact
    return sorted((int(year), float(fact.value)) for year, fact in best.items())


def _capex_by_year(facts: Sequence[FundamentalFact], as_of: dt.date) -> dict[int, float]:
    """Capex per fiscal year from whichever alias scheme the issuer files.

    Preference: a single primary alias (PP&E or ProductiveAssets) wins outright;
    otherwise the split concepts (OtherProductiveAssets, JV interests) are summed
    on top of the primary when an issuer reports capex across several tags.
    """
    primary: dict[int, float] = {}
    for alias in _CAPEX_ALIASES:
        series = _annual_series(facts, alias, as_of)
        if series:
            primary = dict(series)
            break
    if not primary:
        return {}
    for split in _CAPEX_SPLIT_CONCEPTS:
        for year, value in _annual_series(facts, split, as_of):
            primary[year] = primary.get(year, 0.0) + value
    return primary


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
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denom
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

    capex_by_year = _capex_by_year(facts, as_of)
    fcf_series: YearSeries = []
    for year, ocf in ocf_series:
        capex = capex_by_year.get(year)
        fcf_series.append((year, ocf - capex if capex is not None else ocf))
    capex_missing = all(year not in capex_by_year for year, _ in ocf_series)
    capex_estimated = False
    if capex_missing:
        # Never silently proxy FCF with OCF: haircut capex at an industry-average
        # share of revenue so the valuation stays conservative, and say so.
        rev_primary = _annual_series(facts, "Revenues", as_of)
        rev_alias = _annual_series(
            facts, "RevenueFromContractWithCustomerExcludingAssessedTax", as_of
        )
        revenue_for_capex = rev_primary if rev_primary else rev_alias
        if revenue_for_capex:
            rev_by_year = dict(revenue_for_capex)
            haircut: dict[int, float] = {}
            for year, _ocf in ocf_series:
                rev = rev_by_year.get(year)
                if rev is not None and rev > 0:
                    haircut[year] = _CAPEX_TO_REVENUE_DEFAULT * rev
            if haircut:
                fcf_series = [
                    (year, ocf - haircut.get(year, _CAPEX_TO_REVENUE_DEFAULT * ocf))
                    for year, ocf in ocf_series
                ]
                capex_estimated = True
                reasons.append(
                    "capex not reported — estimated at "
                    f"{_CAPEX_TO_REVENUE_DEFAULT:.0%} of revenue (conservative haircut)"
                )
            else:
                reasons.append("capex not reported — FCF proxied by operating cash flow")
        else:
            reasons.append("capex not reported — FCF proxied by operating cash flow")

    base_year, base_fcf = fcf_series[-1]

    # ---- Growth anchor -------------------------------------------------------
    # Pick the revenue alias whose latest fiscal year is freshest — some
    # issuers stopped filing "Revenues" years ago (MSFT after FY2010, AAPL
    # after FY2018) and switched to the contract-revenue tag, so a plain
    # "Revenues or alias" preference would anchor growth on a stale series.
    rev_primary = _annual_series(facts, "Revenues", as_of)
    rev_alias = _annual_series(
        facts, "RevenueFromContractWithCustomerExcludingAssessedTax", as_of
    )
    revenue: YearSeries = rev_primary
    if rev_alias and (not rev_primary or rev_alias[-1][0] > rev_primary[-1][0]):
        revenue = rev_alias
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
            "latest FCF is negative and no positive growth anchor — a DCF here would be fiction"
        )
        return _none_result(reasons, base_fcf=base_fcf)

    # ---- PV of the explicit stage -------------------------------------------
    # Growth fades linearly from the anchor down to the terminal rate across the
    # explicit window — a 10-year cliff-free glide instead of 5 years at one
    # flat rate followed by an abrupt drop to perpetuity growth.
    pv_explicit = 0.0
    flows: list[dict[str, float]] = []
    fcf = base_fcf
    for offset in range(1, _EXPLICIT_YEARS + 1):
        fade = (offset - 1) / max(_EXPLICIT_YEARS - 1, 1)
        year_growth = growth_v + (term_v - growth_v) * fade
        fcf = fcf * (1.0 + year_growth)
        pv = fcf / (1.0 + wacc_v) ** offset
        pv_explicit += pv
        flows.append(
            {"year": float(base_year + offset), "fcf": fcf, "pv": pv, "growth": year_growth}
        )

    # ---- Terminal value (Gordon) --------------------------------------------
    terminal_fcf = fcf * (1.0 + term_v)
    terminal_value = terminal_fcf / (wacc_v - term_v)
    pv_terminal = terminal_value / (1.0 + wacc_v) ** _EXPLICIT_YEARS
    ev = pv_explicit + pv_terminal

    # ---- Terminal sanity check (exit multiple) -------------------------------
    # Cross-check the perpetuity with a conservative EV/FCF multiple applied to
    # the final explicit-year FCF; users see both so the TV is not a black box.
    exit_multiple = 1.0 / (wacc_v - term_v)  # ~15.4x at defaults
    exit_multiple_value = fcf * exit_multiple
    pv_exit_multiple = exit_multiple_value / (1.0 + wacc_v) ** _EXPLICIT_YEARS
    terminal_weight = pv_terminal / ev if ev else None
    if terminal_weight is not None and terminal_weight > _TERMINAL_WEIGHT_WARN:
        reasons.append(
            f"terminal value is {terminal_weight:.0%} of enterprise value — "
            "the estimate leans heavily on the perpetuity assumption"
        )

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
    if debt_by_year:
        # Prefer filed borrowings whenever present — total liabilities include
        # operating items (deferred revenue, payables, leases) that are not
        # debt, which would overstate net debt for non-financials.
        net_debt = debt_by_year[max(debt_by_year)] - cash
        bridge = "filed debt − cash"
        if capex_missing:
            bridge += " (financial: liabilities not netted)"
    elif equity_fact is not None and liabilities_fact is not None:
        # No filed debt concepts at all: fall back to total liabilities minus
        # cash as a conservative net-debt stand-in.
        net_debt = liabilities_fact.value - cash
        bridge = "liabilities − cash"
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
        "terminal_weight": terminal_weight,
        "exit_multiple": exit_multiple,
        "exit_multiple_value": exit_multiple_value,
        "pv_exit_multiple": pv_exit_multiple,
        "capex_estimated": capex_estimated,
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
        "exit_multiple": None,
        "exit_multiple_value": None,
        "pv_exit_multiple": None,
        "capex_estimated": False,
        "flows": [],
        "verdict": None,
    }
