"""Data-quality gate tests (Pandera validation + quarantine)."""

from __future__ import annotations

import pandas as pd

from stock_monitor.features.schema import validate_features


def _base_rows() -> list[dict]:
    return [
        # valid, full fundamentals
        {"ticker": "AAA", "mom_12_1": 0.2, "mom_6_1": 0.1, "vol_3m": 0.3,
         "rsi_14": 55.0, "trend_200": 0.12, "roe": 0.15, "debt_ratio": 0.4,
         "profit_margin": 0.2, "earnings_yield": 0.05, "fcf_yield": 0.04,
         "sentiment": 0.0},
        # valid, missing fundamentals (nullable) -> must pass
        {"ticker": "BBB", "mom_12_1": 0.1, "mom_6_1": 0.05, "vol_3m": 0.25,
         "rsi_14": 48.0, "trend_200": -0.03, "roe": float("nan"),
         "debt_ratio": float("nan"), "profit_margin": float("nan"),
         "earnings_yield": float("nan"), "fcf_yield": float("nan"),
         "sentiment": 0.0},
    ]


def test_valid_rows_pass_including_nan_fundamentals() -> None:
    df = pd.DataFrame(_base_rows())
    valid, quarantined, report = validate_features(df)
    assert report.valid == 2
    assert quarantined.empty
    assert set(valid["ticker"]) == {"AAA", "BBB"}


def test_out_of_range_and_nan_momentum_are_quarantined() -> None:
    rows = _base_rows() + [
        {"ticker": "BAD", "mom_12_1": 99.0, "mom_6_1": 0.05, "vol_3m": -1.0,
         "rsi_14": 60.0, "trend_200": 0.1, "roe": 0.1, "debt_ratio": 0.3,
         "profit_margin": 0.1, "earnings_yield": 0.05, "fcf_yield": 0.04,
         "sentiment": 0.0},
        {"ticker": "NANMOM", "mom_12_1": float("nan"), "mom_6_1": 0.05, "vol_3m": 0.2,
         "rsi_14": 50.0, "trend_200": 0.1, "roe": 0.1, "debt_ratio": 0.3,
         "profit_margin": 0.1, "earnings_yield": 0.05, "fcf_yield": 0.04,
         "sentiment": 0.0},
    ]
    df = pd.DataFrame(rows)
    valid, quarantined, report = validate_features(df)

    assert report.total == 4
    assert report.quarantined == 2
    assert set(valid["ticker"]) == {"AAA", "BBB"}
    assert set(quarantined["ticker"]) == {"BAD", "NANMOM"}
    assert "quarantine_reason" in quarantined.columns
    assert quarantined.set_index("ticker").loc["NANMOM", "quarantine_reason"]


def test_empty_frame_is_handled() -> None:
    valid, quarantined, report = validate_features(pd.DataFrame())
    assert report.total == 0
    assert valid.empty and quarantined.empty
