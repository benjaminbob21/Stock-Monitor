"""Data-quality gate at ingestion (build-plan §1.4).

Bad data -> false confidence -> real money lost. Every feature row is validated on
the way in; rows that fail are *quarantined* (kept aside with a reason) rather than
scored. Price-derived features (momentum, volatility) are always present in a built
row, so they are non-nullable; fundamentals may legitimately be missing (NaN) when a
company hasn't filed the needed line item, so those columns are nullable.

Bounds are deliberately generous — they catch absurd/corrupt values (inf, a 9000%
"return", a negative volatility) without quarantining legitimate outliers.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

# Price return floor is -1.0 (a stock can't fall below zero); allow large upside.
_RETURN_BOUNDS = Check.in_range(-1.0, 50.0)

FEATURE_SCHEMA = DataFrameSchema(
    {
        "ticker": Column(str, nullable=False),
        "mom_12_1": Column(float, _RETURN_BOUNDS, nullable=False),
        "mom_6_1": Column(float, _RETURN_BOUNDS, nullable=False),
        "vol_3m": Column(float, Check.in_range(0.0, 10.0), nullable=False),
        "rsi_14": Column(float, Check.in_range(0.0, 100.0), nullable=False),
        "trend_200": Column(float, _RETURN_BOUNDS, nullable=False),
        "roe": Column(float, Check.in_range(-10.0, 10.0), nullable=True),
        "debt_ratio": Column(float, Check.in_range(0.0, 10.0), nullable=True),
        "profit_margin": Column(float, Check.in_range(-10.0, 10.0), nullable=True),
        "earnings_yield": Column(float, Check.in_range(-2.0, 2.0), nullable=True),
        "fcf_yield": Column(float, Check.in_range(-2.0, 2.0), nullable=True),
        "sentiment": Column(float, Check.in_range(-1.0, 1.0), nullable=True),
    },
    strict=False,  # extra columns (as_of, label, ...) pass through untouched.
    coerce=True,
)


@dataclass(frozen=True)
class ValidationReport:
    """Summary of a validation pass, for data-quality logging."""

    total: int
    valid: int
    quarantined: int

    @property
    def quarantine_rate(self) -> float:
        return self.quarantined / self.total if self.total else 0.0


def validate_features(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, ValidationReport]:
    """Split ``df`` into (valid, quarantined) frames plus a report.

    Uses lazy validation to collect *all* failing rows in one pass. Failing rows are
    returned separately with a ``quarantine_reason`` column; they are never scored.
    """
    if df.empty:
        empty = df.copy()
        return empty, empty, ValidationReport(total=0, valid=0, quarantined=0)

    bad_index: set = set()
    reasons: dict = {}
    try:
        FEATURE_SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        cases = exc.failure_cases
        for idx, column, value in zip(
            cases.get("index"), cases.get("column"), cases.get("failure_case"), strict=False
        ):
            if idx is None or (isinstance(idx, float) and pd.isna(idx)):
                continue
            bad_index.add(idx)
            reasons.setdefault(idx, []).append(f"{column}={value}")

    quarantined = df.loc[df.index.isin(bad_index)].copy()
    if not quarantined.empty:
        quarantined["quarantine_reason"] = [
            "; ".join(reasons.get(idx, ["schema"])) for idx in quarantined.index
        ]
    valid = df.loc[~df.index.isin(bad_index)].copy()

    report = ValidationReport(
        total=len(df), valid=len(valid), quarantined=len(quarantined)
    )
    return valid, quarantined, report
