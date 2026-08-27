"""Tests for allocation contracts (pure dataclasses + invariants)."""

from __future__ import annotations

from datetime import datetime

from stock_monitor.allocation import (
    AllocationConstraints,
    AllocationPlan,
    PortfolioState,
    PositionAllocation,
    PositionInput,
)


def test_position_input_risk_penalty_scales_and_caps() -> None:
    clean = PositionInput(ticker="AAPL", conviction=70.0, volatility=0.25)
    one = PositionInput(ticker="AAPL", conviction=70.0, volatility=0.25, risk_flags=("debt",))
    four = PositionInput(
        ticker="AAPL",
        conviction=70.0,
        volatility=0.25,
        risk_flags=("debt", "dilution", "slowing growth", "guidance cut"),
    )
    assert clean.risk_penalty == 1.0
    assert one.risk_penalty == 0.90
    # 4 flags would be 0.6, but the cap holds it at 0.70.
    assert four.risk_penalty == 0.70


def test_position_allocation_delta() -> None:
    a = PositionAllocation(ticker="MSFT", target_weight=0.12, current_weight=0.05, conviction=80.0)
    assert abs(a.delta_weight - 0.07) < 1e-9


def test_plan_holds_constraints_and_state() -> None:
    constraints = AllocationConstraints(max_per_position=0.15, cash_floor=0.10)
    allocs = (
        PositionAllocation(ticker="NVDA", target_weight=0.15, current_weight=0.0, conviction=85.0),
        PositionAllocation(ticker="XOM", target_weight=0.08, current_weight=0.0, conviction=60.0),
    )
    plan = AllocationPlan(
        as_of=datetime(2026, 8, 27, 14, 0, 0),
        total_value=10_000.0,
        allocations=allocs,
        cash_weight=0.77,
        constraints=constraints,
        warnings=("aggregate conviction weak",),
    )
    assert plan.total_value == 10_000.0
    assert plan.constraints is constraints
    assert sum(a.target_weight for a in plan.allocations) + plan.cash_weight == 1.0
    assert plan.warnings == ("aggregate conviction weak",)


def test_portfolio_state_tracks_current_weights() -> None:
    state = PortfolioState(total_value=5_000.0, positions=(("NVDA", 0.4), ("CASH", 0.6)))
    assert dict(state.positions)["NVDA"] == 0.4
    assert state.total_value == 5_000.0
