"""Feature-builder tests using synthetic data (no network)."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from stock_monitor.features.builder import (
    FEATURE_COLUMNS,
    build_feature_row,
    build_training_frame,
)
from stock_monitor.providers.base import FundamentalFact


def _synthetic_prices(days: int = 600, drift: float = 0.0005) -> pd.DataFrame:
    idx = pd.bdate_range("2021-01-01", periods=days)
    close = 100.0 * np.exp(np.cumsum(np.full(days, drift)))
    return pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.full(days, 1_000_000),
        },
        index=idx,
    )


def _facts() -> list[FundamentalFact]:
    end, filed = dt.date(2021, 12, 31), dt.date(2022, 2, 1)

    def fact(concept: str, value: float) -> FundamentalFact:
        return FundamentalFact("T", concept, value, "USD", end, filed, "10-K")

    return [
        fact("NetIncomeLoss", 50.0),
        fact("StockholdersEquity", 500.0),
        fact("Assets", 1000.0),
        fact("Liabilities", 400.0),
        fact("Revenues", 250.0),
        fact("NetCashProvidedByUsedInOperatingActivities", 90.0),
        fact("PaymentsToAcquirePropertyPlantAndEquipment", 20.0),
        fact("CommonStockSharesOutstanding", 1000.0),
    ]


def test_build_feature_row_none_when_history_too_short() -> None:
    prices = _synthetic_prices(days=100)
    row = build_feature_row("T", prices, [], prices.index[-1].date())
    assert row is None


def test_build_feature_row_computes_expected_ratios() -> None:
    prices = _synthetic_prices()
    as_of = prices.index[-1].date()
    row = build_feature_row("T", prices, _facts(), as_of)

    assert row is not None
    for col in FEATURE_COLUMNS:
        assert col in row
    # roe = 50/500, debt = 400/1000, margin = 50/250
    assert row["roe"] == 0.1
    assert row["debt_ratio"] == 0.4
    assert row["profit_margin"] == 0.2
    assert row["fundamentals_known_on"] == dt.date(2022, 2, 1)
    # Upward drift -> positive momentum.
    assert row["mom_12_1"] > 0
    # Technicals: RSI is bounded; a rising series trends above its 200-day SMA.
    assert 0.0 <= float(row["rsi_14"]) <= 100.0
    assert float(row["trend_200"]) > 0
    # Valuation present and finite; sentiment is the neutral placeholder.
    assert math.isfinite(float(row["earnings_yield"]))
    assert math.isfinite(float(row["fcf_yield"]))
    assert row["sentiment"] == 0.0


def test_training_frame_has_labels_and_features() -> None:
    prices = _synthetic_prices(days=900)
    benchmark = _synthetic_prices(days=900, drift=0.0001)  # ticker outperforms
    frame = build_training_frame("T", prices, _facts(), benchmark, label_window_months=12)

    assert not frame.empty
    assert "label" in frame.columns
    assert set(FEATURE_COLUMNS).issubset(frame.columns)
    # Ticker drifts faster than benchmark -> should mostly beat it.
    assert frame["label"].mean() > 0.5
