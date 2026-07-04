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
from stock_monitor.models.scorer import (
    Scoreable,
    predict_conviction,
    recommendation_band,
    score_row,
)
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

# Hard caps: a red flag ceilings the score no matter how bullish the model is
# (build-plan §7 Phase 3). Better to under-rank a landmine than chase it.
_PENNY_PRICE = 5.0
_EXTREME_VOL = 0.80
_PENNY_CAP = 15
_EXTREME_VOL_CAP = 40
_NO_FUNDAMENTALS_CAP = 50


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


def apply_risk_caps(
    conviction: int, row: dict[str, object], price: float | None
) -> tuple[int, list[str]]:
    """Ceiling the conviction when a hard risk flag fires (build-plan §7 Phase 3).

    Returns the (possibly lowered) conviction and the list of caps that fired. A cap
    never *raises* a score — it only protects against ranking a landmine near the top.
    """
    capped = conviction
    caps: list[str] = []

    if price is not None and price < _PENNY_PRICE:
        capped = min(capped, _PENNY_CAP)
        caps.append("penny_stock_cap")

    vol = _as_float(row.get("vol_3m"))
    if vol == vol and vol > _EXTREME_VOL:
        capped = min(capped, _EXTREME_VOL_CAP)
        caps.append("extreme_volatility_cap")

    if row.get("fundamentals_known_on") is None:
        capped = min(capped, _NO_FUNDAMENTALS_CAP)
        caps.append("no_fundamentals_cap")

    return capped, caps


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
    short_model: Scoreable | None = None,
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
    # Apply the same hard risk caps the scan uses, so the on-demand card and the
    # ranked list always agree for a given ticker.
    price = float(prices["close"].iloc[-1])
    capped, caps = apply_risk_caps(result.conviction, row, price)
    flags = risk_flags(row) + caps
    recommendation = recommendation_band(capped)
    drivers = [
        {"feature": d.feature, "value": d.value, "shap": d.shap, "direction": d.direction}
        for d in result.drivers
    ]
    known_on = row.get("fundamentals_known_on")
    known_on_date = known_on if isinstance(known_on, dt.date) else None

    conviction_3m: int | None = None
    recommendation_3m: str | None = None
    if short_model is not None:
        raw_3m = predict_conviction(short_model, row)
        conviction_3m, _ = apply_risk_caps(raw_3m, row, price)
        recommendation_3m = recommendation_band(conviction_3m)

    if storage is not None:
        storage.upsert_features(valid)
        storage.insert_score(
            ticker=ticker,
            as_of=as_of,
            conviction=capped,
            recommendation=recommendation,
            model_version=model_version,
            fundamentals_known_on=known_on_date,
            drivers=drivers,
            risk_flags=flags,
        )

    return {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "conviction": capped,
        "raw_conviction": result.conviction,
        "price": price,
        "recommendation": recommendation,
        "calibrated": result.calibrated,
        "model_version": model_version,
        "fundamentals_known_on": known_on_date.isoformat() if known_on_date else None,
        "drivers": drivers,
        "risk_flags": flags,
        # Near-term (3-month) read alongside the 12-month conviction, when available.
        "conviction_3m": conviction_3m,
        "recommendation_3m": recommendation_3m,
        "disclaimer": _GUARDRAIL
        + (_CALIBRATED_NOTE if result.calibrated else _UNCALIBRATED_NOTE),
    }


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


# A recommendation only surfaces when the tool is genuinely confident, so seeing one
# means something. "Confident" = a high *calibrated* conviction with no risk caps.
STRONG_CONVICTION = 80


def strong_recommendations(
    opportunities: list[dict], threshold: int = STRONG_CONVICTION
) -> list[dict]:
    """Filter a ranking down to only high-confidence, clean 'consider buying' names.

    Each kept row gets a plain-language ``rationale`` (the expert-persona 'why').
    Deliberately sparse: most scans return few or none — that's the point.
    """
    strong: list[dict] = []
    for opp in opportunities:
        caps = [f for f in opp.get("risk_flags", []) if f.endswith("_cap")]
        if (
            opp.get("capped_conviction", 0) >= threshold
            and not caps
            and opp.get("recommendation") == "consider buying"
        ):
            row = dict(opp)
            row["rationale"] = (
                f"Calibrated conviction {opp['capped_conviction']}/100 with no risk "
                "caps — a high-confidence read (a probability estimate, not a guarantee)."
            )
            strong.append(row)
    return strong
