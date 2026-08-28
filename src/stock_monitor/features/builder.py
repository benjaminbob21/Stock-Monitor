"""Feature builder — assembles a point-in-time-correct feature row per ticker.

Every row is built *as of* a specific date and may only use data knowable on that
date: prices up to ``as_of`` and fundamentals whose ``known_on`` (filing) date is
on or before ``as_of``. The ``fundamentals_known_on`` field records the freshest
filing date actually used, so any row is auditable for look-ahead bias.

Phase 1 feature set — a multi-factor row spanning several pillars (build-plan §2):
- momentum : ``mom_12_1`` (12m-ago -> 1m-ago), ``mom_6_1`` (6m-ago -> 1m-ago).
- volatility: ``vol_3m`` (annualised std of daily returns over ~3 months).
- technicals: ``rsi_14`` (Wilder RSI), ``trend_200`` (price vs 200-day SMA).
- quality  : ``roe`` (NI/equity), ``debt_ratio`` (liabilities/assets),
             ``profit_margin`` (NI/revenues).
- valuation: ``earnings_yield`` (NI/market cap), ``fcf_yield`` (FCF/market cap).
- sentiment: ``sentiment`` — a neutral placeholder reserving the pillar for the
             Phase 4 FinBERT news signal (0.0 until then).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd
import pandas_ta as ta

from stock_monitor.providers.base import FundamentalFact

FEATURE_COLUMNS: tuple[str, ...] = (
    "mom_12_1",
    "mom_6_1",
    "vol_3m",
    "rsi_14",
    "trend_200",
    "roe",
    "debt_ratio",
    "profit_margin",
    "earnings_yield",
    "fcf_yield",
    "sentiment",
)

# Trading-day offsets (~21 sessions per month).
_LOOKBACK_1M = 21
_LOOKBACK_6M = 126
_LOOKBACK_12M = 252
_VOL_WINDOW = 63
_RSI_WINDOW = 14
_SMA_WINDOW = 200

# Revenue may be reported under either concept; prefer the general one.
_REVENUE_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")

# Net income aliases: some issuers (e.g. Mastercard) file 10-Q income only under
# ProfitLoss, while NetIncomeLoss may carry DEF 14A pay-versus-performance annuals.
_NI_CONCEPTS = ("NetIncomeLoss", "ProfitLoss")

# Flow concepts are reported for durations (quarter/YTD/FY) and must be summed
# over a trailing twelve months before entering any ratio. Balance-sheet items
# are instants and stay as latest snapshots.
_FLOW_CONCEPTS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
)

_FILING_FORMS = ("10-K", "10-Q")


def _flow_facts(
    facts: Sequence[FundamentalFact], concept: str, as_of: dt.date
) -> list[FundamentalFact]:
    """PIT-eligible facts for a flow concept, filing forms filtered when known.

    When any fact carries a 10-K/10-Q form tag, facts from other filings (DEF 14A
    pay-versus-performance annuals, 8-Ks) are excluded — those repeat FY totals
    out of cycle and would corrupt TTM math. Without any form info, keep all.
    Dedupe (collapsing comparative re-filings of the same period) happens after
    the PIT filter so historical as-of dates still see the originally-filed row.
    """
    eligible = [f for f in facts if f.concept == concept and f.known_on <= as_of]
    if not eligible:
        return []
    if any(f.form in _FILING_FORMS for f in eligible):
        eligible = [f for f in eligible if f.form in _FILING_FORMS]
    best: dict[tuple[dt.date | None, dt.date, float], FundamentalFact] = {}
    for f in eligible:
        key = (f.period_start, f.fiscal_end, f.value)
        prior = best.get(key)
        if prior is None or f.known_on > prior.known_on:
            best[key] = f
    return list(best.values())


def _duration_days(fact: FundamentalFact) -> int | None:
    """Length of the reported period in days, when the source provides a start."""
    if fact.period_start is None:
        return None
    return (fact.fiscal_end - fact.period_start).days


def _pick_ytd(
    facts: list[FundamentalFact],
) -> tuple[FundamentalFact, list[FundamentalFact]] | None:
    """Pick the freshest fiscal period and all facts covering it.

    Ties (e.g. the standalone quarter and the cumulative YTD share the same
    period end) are resolved by preferring the longest duration, then the
    latest filing — the cumulative total is the TTM building block.
    """
    if not facts:
        return None
    freshest_end = max(f.fiscal_end for f in facts)
    covering = [f for f in facts if f.fiscal_end == freshest_end]

    def _rank(f: FundamentalFact) -> tuple[int, dt.date]:
        duration = _duration_days(f) or 0
        return duration, f.known_on

    return max(covering, key=_rank), covering


def _prior_ytd(facts: list[FundamentalFact], current: FundamentalFact) -> FundamentalFact | None:
    """Find last year's comparable cumulative period for a YTD fact.

    Matches on period *length* (e.g. H1 vs H1) ending in the prior fiscal year,
    allowing calendar drift of a few days around the anniversary.
    """
    length = _duration_days(current)
    if length is None:
        return None
    prior_year = current.fiscal_end.year - 1
    candidates = [
        f
        for f in facts
        if f.period_start is not None
        and f.fiscal_end.year == prior_year
        and abs((_duration_days(f) or 0) - length) <= 5
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.known_on)


def _is_annual(fact: FundamentalFact) -> bool:
    """True when the fact covers a full fiscal year.

    Uses the reported period length when available; falls back to the 10-K form
    tag for legacy data without period starts.
    """
    duration = _duration_days(fact)
    if duration is not None:
        return duration >= 300
    return fact.form == "10-K"


def _annual_fact(facts: list[FundamentalFact]) -> FundamentalFact | None:
    """Latest full-fiscal-year fact, most recent period end then filing date."""
    annuals = [f for f in facts if _is_annual(f)]
    if not annuals:
        return None
    return max(annuals, key=lambda f: (f.fiscal_end, f.known_on))


def _ttm_flow(
    facts: Sequence[FundamentalFact], concept: str, as_of: dt.date
) -> tuple[float | None, dt.date | None]:
    """Trailing-twelve-month value for a flow concept, PIT-correct.

    TTM = latest full fiscal year + current-year YTD − prior-year same YTD.
    This is the standard construction because Q4 is never filed standalone
    (the 10-K reports the full year, not the fourth quarter).

    Fallbacks, in order:
    - the annual itself when the freshest known period *is* a full year;
    - annual-only when the prior-year YTD comparative is missing;
    - trailing sum of the last four standalone quarters when no annual is
      knowable yet (young listings);
    - ``None`` when nothing PIT-eligible exists.

    Returns ``(value, known_on_of_freshest_input)``.
    """
    eligible = _flow_facts(facts, concept, as_of)
    if not eligible:
        return None, None

    picked = _pick_ytd(eligible)
    if picked is None:
        return None, None
    current, _covering = picked

    # Path A: the freshest known period is a full fiscal year -> it IS the TTM.
    if _is_annual(current):
        return current.value, current.known_on

    annual = _annual_fact(eligible)
    prior = _prior_ytd(eligible, current)

    # Path B: annual + YTD − prior YTD. The annual must be a *completed prior*
    # fiscal year (period end strictly before the current period's end), which
    # also handles non-calendar fiscal years (e.g. FY ending September).
    if annual is not None and prior is not None and annual.fiscal_end < current.fiscal_end:
        ttm = annual.value + current.value - prior.value
        return ttm, max(current.known_on, annual.known_on, prior.known_on)

    # Path C: annual-only — the annual is known but the prior-year YTD
    # comparative is missing (brief window right after an unusual Q1).
    if annual is not None and annual.fiscal_end < current.fiscal_end:
        return annual.value, annual.known_on

    # Path D: trailing four standalone quarters (young listings without a 10-K).
    quarters = [
        f for f in eligible if (duration := _duration_days(f)) is not None and 60 <= duration <= 120
    ]
    if len(quarters) >= 4:
        ends = sorted({q.fiscal_end for q in quarters}, reverse=True)[:4]
        by_end: dict[dt.date, FundamentalFact] = {}
        for q in quarters:
            if q.fiscal_end not in ends:
                continue
            prior_q = by_end.get(q.fiscal_end)
            if prior_q is None or abs(q.value) >= abs(prior_q.value):
                by_end[q.fiscal_end] = q
        ttm = sum(q.value for q in by_end.values())
        return ttm, max(q.known_on for q in by_end.values())

    # Path E: nothing usable (only stale annuals or sub-annual fragments).
    return None, None


def _ttm_flow_alias(
    facts: Sequence[FundamentalFact], concepts: Sequence[str], as_of: dt.date
) -> tuple[float | None, dt.date | None]:
    """TTM across concept aliases; the freshest-known result wins.

    Issuers report flows under different us-gaap tags (NetIncomeLoss vs
    ProfitLoss, Revenues vs RevenueFromContractWithCustomer…). Each alias is
    converted to TTM independently and the one whose inputs are most recently
    filed is used, so an alias that stopped receiving data (e.g. net income
    living only in DEF 14A annuals) cannot shadow a live one.
    """
    best: tuple[float, dt.date] | None = None
    for concept in concepts:
        value, known = _ttm_flow(facts, concept, as_of)
        if value is None or known is None:
            continue
        if best is None or known > best[1]:
            best = (value, known)
    return best if best else (None, None)


def _price_on_or_before(prices: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    window = prices.loc[:ts, "close"]
    return float(window.iloc[-1]) if not window.empty else None


def _price_on_or_after(prices: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    window = prices.loc[ts:, "close"]
    return float(window.iloc[0]) if not window.empty else None


def latest_fact(
    facts: Sequence[FundamentalFact], concept: str, as_of: dt.date
) -> FundamentalFact | None:
    """Return the freshest fact for ``concept`` knowable on ``as_of``.

    Only facts with ``known_on <= as_of`` are eligible (the PIT rule). Among those,
    pick the one describing the most recent fiscal period, tie-broken by filing date.
    """
    eligible = [f for f in facts if f.concept == concept and f.known_on <= as_of]
    if not eligible:
        return None
    return max(eligible, key=lambda f: (f.fiscal_end, f.known_on))


def _latest_value(
    facts: Sequence[FundamentalFact], concept: str, as_of: dt.date
) -> tuple[float | None, dt.date | None]:
    fact = latest_fact(facts, concept, as_of)
    return (fact.value, fact.known_on) if fact else (None, None)


def _safe_ratio(numerator: float | None, denominator: float | None) -> float:
    if numerator is None or denominator is None or denominator == 0:
        return math.nan
    return numerator / denominator


def build_feature_row(
    ticker: str,
    prices: pd.DataFrame,
    facts: Sequence[FundamentalFact],
    as_of: dt.date,
    sentiment_lookup: Callable[[dt.date], float] | None = None,
) -> dict[str, object] | None:
    """Build a single PIT-correct feature row, or ``None`` if history is too short.

    NaN feature values are intentional where a fundamental is unavailable — LightGBM
    handles missing values natively, and a missing pillar must never fabricate a number.

    ``sentiment_lookup``, when provided, returns the PIT news sentiment knowable on
    ``as_of`` (see :func:`stock_monitor.backfill.make_sentiment_lookup`). It defaults to
    the neutral 0.0 placeholder, preserving behaviour when no backfill exists.
    """
    as_of_ts = pd.Timestamp(as_of)
    window = prices.loc[:as_of_ts]
    if len(window) < _LOOKBACK_12M + 1:
        return None

    close = window["close"]
    p_1m = float(close.iloc[-_LOOKBACK_1M])
    p_6m = float(close.iloc[-_LOOKBACK_6M])
    p_12m = float(close.iloc[-_LOOKBACK_12M])
    p_last = float(close.iloc[-1])

    daily_returns = close.iloc[-_VOL_WINDOW:].pct_change().dropna()
    vol_3m = float(daily_returns.std() * math.sqrt(252)) if not daily_returns.empty else math.nan

    rsi_series = ta.rsi(close, length=_RSI_WINDOW)
    rsi_14 = (
        float(rsi_series.iloc[-1])
        if rsi_series is not None and not rsi_series.dropna().empty
        else math.nan
    )
    sma_200 = float(close.iloc[-_SMA_WINDOW:].mean())
    trend_200 = p_last / sma_200 - 1.0 if sma_200 else math.nan

    # Flows are trailing-twelve-month sums (PIT-correct); balance-sheet items
    # stay as latest instants. Net income falls back from NetIncomeLoss to
    # ProfitLoss (issuers like Mastercard file quarterly income only there).
    net_income, k1 = _ttm_flow_alias(facts, _NI_CONCEPTS, as_of)
    equity, k2 = _latest_value(facts, "StockholdersEquity", as_of)
    assets, k3 = _latest_value(facts, "Assets", as_of)
    liabilities, k4 = _latest_value(facts, "Liabilities", as_of)
    revenues, k5 = _ttm_flow_alias(facts, _REVENUE_CONCEPTS, as_of)
    ocf, k6 = _ttm_flow(facts, _FLOW_CONCEPTS[0], as_of)
    capex, k7 = _ttm_flow(facts, _FLOW_CONCEPTS[1], as_of)
    shares, k8 = _latest_value(facts, "CommonStockSharesOutstanding", as_of)

    market_cap = p_last * shares if shares else None
    free_cash_flow = ocf - capex if ocf is not None and capex is not None else None

    known_dates = [k for k in (k1, k2, k3, k4, k5, k6, k7, k8) if k is not None]
    fundamentals_known_on = max(known_dates) if known_dates else None

    return {
        "ticker": ticker.upper(),
        "as_of": as_of,
        "fundamentals_known_on": fundamentals_known_on,
        "mom_12_1": p_1m / p_12m - 1.0,
        "mom_6_1": p_1m / p_6m - 1.0,
        "vol_3m": vol_3m,
        "rsi_14": rsi_14,
        "trend_200": trend_200,
        "roe": _safe_ratio(net_income, equity),
        "debt_ratio": _safe_ratio(liabilities, assets),
        "profit_margin": _safe_ratio(net_income, revenues),
        "earnings_yield": _safe_ratio(net_income, market_cap),
        "fcf_yield": _safe_ratio(free_cash_flow, market_cap),
        "sentiment": float(sentiment_lookup(as_of)) if sentiment_lookup else 0.0,
    }


def build_training_frame(
    ticker: str,
    prices: pd.DataFrame,
    facts: Sequence[FundamentalFact],
    benchmark_prices: pd.DataFrame,
    label_window_months: int,
    step_months: int = 1,
    sentiment_lookup: Callable[[dt.date], float] | None = None,
) -> pd.DataFrame:
    """Build a labelled frame by walking monthly as-of dates through history.

    Label = 1 if the ticker's forward ``label_window_months`` return beats the
    benchmark's over the same window, else 0. Both features and label are computed
    PIT-correctly: no row can see data past its own ``as_of``.

    Pass ``sentiment_lookup`` to bake the backfilled PIT news sentiment into each row's
    ``sentiment`` feature; omit it to keep the neutral 0.0 placeholder.
    """
    if prices.empty:
        return pd.DataFrame(columns=[*FEATURE_COLUMNS, "label", "as_of", "fundamentals_known_on"])

    grid = pd.date_range(prices.index[0], prices.index[-1], freq=f"{step_months}MS")
    rows: list[dict[str, object]] = []

    for as_of_ts in grid:
        as_of = as_of_ts.date()
        row = build_feature_row(ticker, prices, facts, as_of, sentiment_lookup)
        if row is None:
            continue

        target_ts = as_of_ts + pd.DateOffset(months=label_window_months)
        p_now = _price_on_or_before(prices, as_of_ts)
        p_future = _price_on_or_after(prices, target_ts)
        b_now = _price_on_or_before(benchmark_prices, as_of_ts)
        b_future = _price_on_or_after(benchmark_prices, target_ts)
        if p_now is None or p_future is None or b_now is None or b_future is None:
            continue
        if p_now == 0 or b_now == 0:
            continue

        fwd_ret = p_future / p_now - 1.0
        bench_ret = b_future / b_now - 1.0
        row["label"] = int(fwd_ret > bench_ret)
        rows.append(row)

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame
