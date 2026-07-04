"""Backtest tests (network-free)."""

from __future__ import annotations

from types import SimpleNamespace

from stock_monitor.backtest import run_backtest


def test_backtest_runs_and_reports(backtest_world: SimpleNamespace) -> None:
    result = run_backtest(
        backtest_world.frame,
        backtest_world.price_frames,
        backtest_world.benchmark,
        top_k=2,
        cost_bps=10.0,
        min_train=20,
    )
    assert result.n_periods > 0
    assert -1.0 <= result.max_drawdown <= 0.0
    assert 0.0 <= result.hit_rate <= 1.0
    assert result.avg_turnover >= 0.0
    assert result.equity_curve
    # Excess return is exactly strategy minus benchmark.
    assert abs(
        result.excess_return
        - (result.strategy_total_return - result.benchmark_total_return)
    ) < 1e-9


def test_backtest_costs_reduce_returns(backtest_world: SimpleNamespace) -> None:
    free = run_backtest(
        backtest_world.frame,
        backtest_world.price_frames,
        backtest_world.benchmark,
        top_k=2,
        cost_bps=0.0,
        min_train=20,
    )
    costly = run_backtest(
        backtest_world.frame,
        backtest_world.price_frames,
        backtest_world.benchmark,
        top_k=2,
        cost_bps=200.0,
        min_train=20,
    )
    assert costly.strategy_total_return <= free.strategy_total_return
