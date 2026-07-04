"""Feature builder — assembles a point-in-time-correct feature row per ticker.

Every row is built *as of* a specific date and may only use data knowable on that
date: prices up to ``as_of`` and fundamentals whose ``known_on`` (filing) date is
on or before ``as_of``. The ``fundamentals_known_on`` field records the freshest
filing date actually used, so any row is auditable for look-ahead bias.

Phase 0 feature set (a few fundamentals + momentum, per build-plan §7):
- ``mom_12_1`` : 12-month-ago -> 1-month-ago price return (classic momentum factor).
- ``mom_6_1``  : 6-month-ago -> 1-month-ago price return.
- ``vol_3m``   : annualised volatility of daily returns over ~3 months.
- ``roe``      : NetIncomeLoss / StockholdersEquity (quality).
- ``debt_ratio``: Liabilities / Assets (balance-sheet risk).
- ``profit_margin``: NetIncomeLoss / Revenues (quality).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

from stock_monitor.providers.base import FundamentalFact

FEATURE_COLUMNS: tuple[str, ...] = (
    "mom_12_1",
    "mom_6_1",
    "vol_3m",
    "roe",
    "debt_ratio",
    "profit_margin",
)

# Trading-day offsets (~21 sessions per month).
_LOOKBACK_1M = 21
_LOOKBACK_6M = 126
_LOOKBACK_12M = 252
_VOL_WINDOW = 63

# Revenue may be reported under either concept; prefer the general one.
_REVENUE_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")


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
    eligible = [
        f for f in facts if f.concept == concept and f.known_on <= as_of
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda f: (f.fiscal_end, f.known_on))


def _latest_value(
    facts: Sequence[FundamentalFact], concept: str, as_of: dt.date
) -> tuple[float | None, dt.date | None]:
    fact = latest_fact(facts, concept, as_of)
    return (fact.value, fact.known_on) if fact else (None, None)


def _revenue(
    facts: Sequence[FundamentalFact], as_of: dt.date
) -> tuple[float | None, dt.date | None]:
    for concept in _REVENUE_CONCEPTS:
        value, known = _latest_value(facts, concept, as_of)
        if value is not None:
            return value, known
    return None, None


def _safe_ratio(numerator: float | None, denominator: float | None) -> float:
    if numerator is None or denominator is None or denominator == 0:
        return math.nan
    return numerator / denominator


def build_feature_row(
    ticker: str,
    prices: pd.DataFrame,
    facts: Sequence[FundamentalFact],
    as_of: dt.date,
) -> dict[str, object] | None:
    """Build a single PIT-correct feature row, or ``None`` if history is too short.

    NaN feature values are intentional where a fundamental is unavailable — LightGBM
    handles missing values natively, and a missing pillar must never fabricate a number.
    """
    as_of_ts = pd.Timestamp(as_of)
    window = prices.loc[:as_of_ts]
    if len(window) < _LOOKBACK_12M + 1:
        return None

    close = window["close"]
    p_1m = float(close.iloc[-_LOOKBACK_1M])
    p_6m = float(close.iloc[-_LOOKBACK_6M])
    p_12m = float(close.iloc[-_LOOKBACK_12M])

    daily_returns = close.iloc[-_VOL_WINDOW:].pct_change().dropna()
    vol_3m = float(daily_returns.std() * math.sqrt(252)) if not daily_returns.empty else math.nan

    net_income, k1 = _latest_value(facts, "NetIncomeLoss", as_of)
    equity, k2 = _latest_value(facts, "StockholdersEquity", as_of)
    assets, k3 = _latest_value(facts, "Assets", as_of)
    liabilities, k4 = _latest_value(facts, "Liabilities", as_of)
    revenues, k5 = _revenue(facts, as_of)

    known_dates = [k for k in (k1, k2, k3, k4, k5) if k is not None]
    fundamentals_known_on = max(known_dates) if known_dates else None

    return {
        "ticker": ticker.upper(),
        "as_of": as_of,
        "fundamentals_known_on": fundamentals_known_on,
        "mom_12_1": p_1m / p_12m - 1.0,
        "mom_6_1": p_1m / p_6m - 1.0,
        "vol_3m": vol_3m,
        "roe": _safe_ratio(net_income, equity),
        "debt_ratio": _safe_ratio(liabilities, assets),
        "profit_margin": _safe_ratio(net_income, revenues),
    }


def build_training_frame(
    ticker: str,
    prices: pd.DataFrame,
    facts: Sequence[FundamentalFact],
    benchmark_prices: pd.DataFrame,
    label_window_months: int,
    step_months: int = 1,
) -> pd.DataFrame:
    """Build a labelled frame by walking monthly as-of dates through history.

    Label = 1 if the ticker's forward ``label_window_months`` return beats the
    benchmark's over the same window, else 0. Both features and label are computed
    PIT-correctly: no row can see data past its own ``as_of``.
    """
    if prices.empty:
        return pd.DataFrame(columns=[*FEATURE_COLUMNS, "label", "as_of", "fundamentals_known_on"])

    grid = pd.date_range(prices.index[0], prices.index[-1], freq=f"{step_months}MS")
    rows: list[dict[str, object]] = []

    for as_of_ts in grid:
        as_of = as_of_ts.date()
        row = build_feature_row(ticker, prices, facts, as_of)
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
