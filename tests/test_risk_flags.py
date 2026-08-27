"""Unit tests for the heuristic risk-flag layer in service.risk_flags."""

from __future__ import annotations

from stock_monitor.service import risk_flags


def _row(**overrides: object) -> dict[str, object]:
    """A clean, healthy mega-cap baseline row — no flags expected."""
    base: dict[str, object] = {
        "fundamentals_known_on": "2026-08-01",
        "vol_3m": 0.25,
        "profit_margin": 0.20,
        "debt_ratio": 0.30,
        "earnings_yield": 0.04,
        "mom_12_1": 0.10,
        "rsi_14": 55.0,
        "trend_200": 0.05,
    }
    base.update(overrides)
    return base


def test_clean_row_has_no_flags() -> None:
    assert risk_flags(_row()) == []


def test_missing_fundamentals_flag() -> None:
    flags = risk_flags(_row(fundamentals_known_on=None))
    assert any(f.startswith("reduced_confidence") for f in flags)


def test_high_volatility_and_negative_earnings() -> None:
    flags = risk_flags(_row(vol_3m=0.70, profit_margin=-0.05))
    assert "high_volatility" in flags
    assert "negative_earnings" in flags


def test_nan_values_do_not_fire() -> None:
    flags = risk_flags(
        _row(
            vol_3m=float("nan"),
            profit_margin=float("nan"),
            debt_ratio=float("nan"),
            earnings_yield=float("nan"),
            rsi_14=float("nan"),
            trend_200=float("nan"),
            mom_12_1=float("nan"),
        )
    )
    # Only the missing-fundamentals flag may fire; numeric NaNs must stay silent.
    assert all(f.startswith("reduced_confidence") for f in flags) or flags == []


def test_leverage_tiers() -> None:
    assert "high_leverage" in risk_flags(_row(debt_ratio=0.65))
    assert "severe_leverage" in risk_flags(_row(debt_ratio=0.85))


def test_valuation_distortion_flags() -> None:
    # Negative earnings yield alone.
    assert "expensive_on_earnings" in risk_flags(_row(earnings_yield=-0.05))
    # Negative yield + hot momentum = the story-stock stretch flag.
    assert "momentum_valuation_stretch" in risk_flags(
        _row(earnings_yield=-0.05, mom_12_1=0.45)
    )
    # But not with weak momentum.
    assert "momentum_valuation_stretch" not in risk_flags(
        _row(earnings_yield=-0.05, mom_12_1=0.05)
    )


def test_rsi_extremes() -> None:
    assert "overbought_rsi" in risk_flags(_row(rsi_14=80.0))
    assert "oversold_rsi" in risk_flags(_row(rsi_14=20.0))
    assert "overbought_rsi" not in risk_flags(_row(rsi_14=60.0))


def test_below_long_term_trend() -> None:
    assert "below_trend_200" in risk_flags(_row(trend_200=-0.08))
