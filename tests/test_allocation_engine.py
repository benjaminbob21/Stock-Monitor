"""Tests for the deterministic allocation engine."""

from __future__ import annotations

import datetime as dt

import pytest

from stock_monitor.allocation import (
    AllocationConstraints,
    PortfolioState,
    PositionInput,
    allocate,
)
from stock_monitor.allocation.engine import AGGREGATE_WEAK, SELL_BELOW


def _pos(
    ticker: str,
    conviction: float,
    vol: float = 0.25,
    news: float | None = None,
    alt: float | None = None,
    flags: tuple[str, ...] = (),
) -> PositionInput:
    return PositionInput(
        ticker=ticker,
        conviction=conviction,
        volatility=vol,
        news_sentiment=news,
        alt_sentiment=alt,
        risk_flags=flags,
    )


def test_weights_sum_to_one_with_cash() -> None:
    plan = allocate(
        [_pos("NVDA", 85), _pos("MSFT", 75, vol=0.3), _pos("XOM", 60, vol=0.2)],
        PortfolioState(total_value=10_000.0, positions=()),
    )
    assert plan.allocations
    assert plan.cash_weight >= 0.0
    assert sum(a.target_weight for a in plan.allocations) + plan.cash_weight == pytest.approx(1.0)


def test_higher_conviction_lower_vol_gets_more() -> None:
    plan = allocate(
        [_pos("STRONG", 90, vol=0.2), _pos("WEAK", 60, vol=0.5)],
        PortfolioState(total_value=10_000.0, positions=()),
        AllocationConstraints(max_per_position=1.0, cash_floor=0.0),
    )
    w = {a.ticker: a.target_weight for a in plan.allocations}
    assert w["STRONG"] > w["WEAK"] * 2


def test_sell_band_excluded_with_warning() -> None:
    plan = allocate(
        [_pos("JUNK", SELL_BELOW - 1), _pos("OK", 70)],
        PortfolioState(total_value=10_000.0, positions=()),
    )
    assert {a.ticker for a in plan.allocations} == {"OK"}
    assert any("sell band" in w and "JUNK" in w for w in plan.warnings)


def test_per_position_cap_returns_excess_to_cash() -> None:
    plan = allocate(
        [_pos("DOMINANT", 95, vol=0.12), _pos("SMALL", 55, vol=0.9)],
        PortfolioState(total_value=10_000.0, positions=()),
        AllocationConstraints(max_per_position=0.4, cash_floor=0.0),
    )
    w = {a.ticker: a.target_weight for a in plan.allocations}
    assert w["DOMINANT"] == pytest.approx(0.4)
    assert sum(w.values()) < 1.0
    assert any("cap" in x for x in plan.warnings)


def test_dust_floor_drops_tiny_positions() -> None:
    plan = allocate(
        [_pos("BIG", 90), _pos("DUST", 41, vol=2.0)],
        PortfolioState(total_value=10_000.0, positions=()),
        AllocationConstraints(min_per_position=0.05, max_per_position=1.0, cash_floor=0.0),
    )
    assert {a.ticker for a in plan.allocations} == {"BIG"}
    assert any("DUST" in w for w in plan.warnings)


def test_cash_floor_full_when_aggregate_weak() -> None:
    plan = allocate(
        [_pos("MED", 45), _pos("MED2", 46)],
        PortfolioState(total_value=10_000.0, positions=()),
        AllocationConstraints(max_per_position=1.0, cash_floor=0.25),
    )
    assert plan.cash_weight == pytest.approx(0.25)


def test_cash_floor_zero_when_aggregate_strong() -> None:
    plan = allocate(
        [_pos("HI", 90), _pos("HI2", 85)],
        PortfolioState(total_value=10_000.0, positions=()),
        AllocationConstraints(max_per_position=1.0, cash_floor=0.25),
    )
    assert plan.cash_weight == pytest.approx(0.0)


def test_risk_flags_shrink_and_are_explained() -> None:
    clean_pos = _pos("CLEAN", 70)
    flagged_pos = _pos("FLAGGED", 70, flags=("debt", "dilution"))
    plan = allocate(
        [clean_pos, flagged_pos],
        PortfolioState(total_value=1_000.0, positions=()),
        AllocationConstraints(max_per_position=1.0, cash_floor=0.0),
    )
    w = {a.ticker: a.target_weight for a in plan.allocations}
    assert w["CLEAN"] > w["FLAGGED"]
    assert any(
        "risk-flag" in r for al in plan.allocations if al.ticker == "FLAGGED" for r in al.reasons
    )


def test_bearish_news_trims_bullish_adds() -> None:
    bull = _pos("BULL", 70, news=0.8)
    bear = _pos("BEAR", 70, news=-0.8)
    plan = allocate(
        [bull, bear],
        PortfolioState(total_value=1_000.0, positions=()),
        AllocationConstraints(max_per_position=1.0, cash_floor=0.0),
    )
    w = {a.ticker: a.target_weight for a in plan.allocations}
    assert w["BULL"] > w["BEAR"]


def test_current_weights_surface_as_deltas() -> None:
    plan = allocate(
        [_pos("HELD", 80)],
        PortfolioState(total_value=5_000.0, positions=(("HELD", 0.3),)),
        AllocationConstraints(max_per_position=1.0, cash_floor=0.0),
    )
    a = plan.allocations[0]
    assert a.current_weight == 0.3
    # One candidate, no cash floor, no cap: the whole book goes to the name.
    assert a.target_weight == pytest.approx(1.0)
    assert a.delta_weight == pytest.approx(0.7)


def test_no_candidates_is_all_cash() -> None:
    plan = allocate(
        [],
        PortfolioState(total_value=1_000.0, positions=()),
    )
    assert plan.allocations == ()
    assert plan.cash_weight == 1.0


def test_max_positions_limits_book() -> None:
    plan = allocate(
        [_pos(f"T{i}", 80 - i) for i in range(12)],
        PortfolioState(total_value=10_000.0, positions=()),
        AllocationConstraints(max_positions=8, max_per_position=1.0, cash_floor=0.0),
    )
    assert len(plan.allocations) == 8
    assert any("max_positions" in w for w in plan.warnings)


def test_reasons_mention_trim_band() -> None:
    plan = allocate(
        [_pos("MID", 50)],
        PortfolioState(total_value=1_000.0, positions=()),
        AllocationConstraints(max_per_position=1.0, cash_floor=0.0),
    )
    assert any("trim" in r for r in plan.allocations[0].reasons)


def test_as_of_is_respected() -> None:
    ts = dt.datetime(2026, 8, 27, 12, 0, 0)
    plan = allocate(
        [_pos("X", 70)],
        PortfolioState(total_value=1_000.0, positions=()),
        as_of=ts,
    )
    assert plan.as_of == ts


def test_aggregate_weak_constant_matches_band_sanity() -> None:
    # The cash interpolation window must sit inside the investable band.
    assert AGGREGATE_WEAK >= SELL_BELOW
