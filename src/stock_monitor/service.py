"""On-demand scoring service — the orchestration the API (and later jobs) reuse.

Fetch prices + PIT fundamentals for one ticker, build the current feature row,
run it through the data-quality gate, score it, attach risk flags, optionally
persist, and return a transparent, JSON-ready payload.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_monitor.features.builder import build_feature_row
from stock_monitor.features.schema import validate_features
from stock_monitor.models.scorer import Scoreable, score_row
from stock_monitor.providers.base import FundamentalProvider, PriceProvider
from stock_monitor.storage.db import Storage

HISTORY_YEARS = 8
_GUARDRAIL = (
    "Decision-support only — you execute every trade (no auto-trading). "
    "Scores are estimates, not guarantees."
)
_UNCALIBRATED_NOTE = (
    " Conviction is uncalibrated (a ranking signal, not a probability) "
    "until enough data supports calibration."
)
_CALIBRATED_NOTE = (
    " Conviction is calibrated on out-of-fold history — treat it as an estimated "
    "probability, still not a certainty."
)

# Risk-flag thresholds (Phase 1, deliberately simple).
_HIGH_VOL = 0.60


class TickerDataUnavailable(Exception):
    """No price history could be fetched for the ticker."""


class InsufficientHistory(Exception):
    """Not enough price history to build a feature row."""


class DataQuarantined(Exception):
    """The ticker's current row failed the data-quality gate."""


def risk_flags(row: dict[str, object]) -> list[str]:
    """Derive lightweight risk flags from a feature row (build-plan: score + flags)."""
    flags: list[str] = []

    if row.get("fundamentals_known_on") is None:
        flags.append("reduced_confidence: no point-in-time fundamentals")

    vol = _as_float(row.get("vol_3m"))
    if vol == vol and vol > _HIGH_VOL:  # not-NaN and elevated
        flags.append("high_volatility")

    margin = _as_float(row.get("profit_margin"))
    if margin == margin and margin < 0:
        flags.append("negative_earnings")

    return flags


def score_ticker(
    ticker: str,
    *,
    model: Scoreable,
    model_version: str,
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    label_window_months: int,
    storage: Storage | None = None,
    today: dt.date | None = None,
) -> dict[str, object]:
    """Score a single ticker on demand and return a JSON-ready payload."""
    ticker = ticker.upper()
    end = today or dt.date.today()
    start = end - dt.timedelta(days=365 * HISTORY_YEARS)

    prices = price_provider.get_prices(ticker, start, end)
    if prices.empty:
        raise TickerDataUnavailable(ticker)

    facts = fundamental_provider.get_fundamentals(ticker)
    as_of = prices.index[-1].date()
    row = build_feature_row(ticker, prices, facts, as_of)
    if row is None:
        raise InsufficientHistory(ticker)

    valid, quarantined, _ = validate_features(pd.DataFrame([row]))
    if valid.empty:
        if storage is not None:
            storage.record_quarantine(quarantined)
        reason = str(quarantined.iloc[0].get("quarantine_reason", "schema"))
        raise DataQuarantined(reason)

    result = score_row(model, row)
    flags = risk_flags(row)
    drivers = [
        {"feature": d.feature, "value": d.value, "shap": d.shap, "direction": d.direction}
        for d in result.drivers
    ]
    known_on = row.get("fundamentals_known_on")
    known_on_date = known_on if isinstance(known_on, dt.date) else None

    if storage is not None:
        storage.upsert_features(valid)
        storage.insert_score(
            ticker=ticker,
            as_of=as_of,
            conviction=result.conviction,
            recommendation=result.recommendation,
            model_version=model_version,
            fundamentals_known_on=known_on_date,
            drivers=drivers,
            risk_flags=flags,
        )

    return {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "conviction": result.conviction,
        "recommendation": result.recommendation,
        "calibrated": result.calibrated,
        "model_version": model_version,
        "fundamentals_known_on": known_on_date.isoformat() if known_on_date else None,
        "drivers": drivers,
        "risk_flags": flags,
        "disclaimer": _GUARDRAIL
        + (_CALIBRATED_NOTE if result.calibrated else _UNCALIBRATED_NOTE),
    }


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
