"""Offline tests for the edge scorecard verdict logic and backtest storage."""

from __future__ import annotations

import datetime as dt

from stock_monitor.scorecard import MIN_CLOSED_PICKS, build_scorecard
from stock_monitor.storage.db import Storage


def _seed_closed_picks(store: Storage, n: int, *, beat: bool) -> None:
    for i in range(n):
        pid = f"T{i}:2024-01-01:12"
        store.record_paper_pick(
            pick_id=pid,
            ticker=f"T{i}",
            pick_date=dt.date(2024, 1, 1),
            conviction=80,
            recommendation="consider buying",
            horizon_months=12,
            entry_price=100.0,
            benchmark_entry=50.0,
            model_version="v",
            matured_on=dt.date(2025, 1, 1),
        )
        store.close_paper_pick(
            pid,
            exit_price=110.0,
            benchmark_exit=52.0,
            stock_return=0.10,
            benchmark_return=0.04,
            excess_return=0.05 if beat else -0.05,
            beat_benchmark=beat,
        )


def _save_backtest(store: Storage, *, excess: float, hit: float) -> None:
    store.save_backtest_result(
        n_periods=59,
        universe_size=48,
        top_k=3,
        cost_bps=10.0,
        strategy_total_return=0.55,
        benchmark_total_return=1.01,
        excess_return=excess,
        strategy_cagr=0.09,
        benchmark_cagr=0.15,
        max_drawdown=-0.37,
        hit_rate=hit,
    )


def test_latest_backtest_roundtrip():
    with Storage(":memory:") as store:
        assert store.latest_backtest() is None
        _save_backtest(store, excess=-0.46, hit=0.458)
        bt = store.latest_backtest()
        assert bt is not None
        assert bt["n_periods"] == 59
        assert bt["universe_size"] == 48
        assert round(bt["excess_return"], 2) == -0.46


def test_verdict_building_when_no_data():
    with Storage(":memory:") as store:
        card = build_scorecard(store)
        assert card["verdict"] == "building"
        assert card["backtest"]["status"] == "pending"
        assert card["paper"]["status"] == "pending"


def test_verdict_no_edge_when_backtest_fails():
    with Storage(":memory:") as store:
        _save_backtest(store, excess=-0.46, hit=0.458)
        card = build_scorecard(store)
        assert card["backtest"]["status"] == "fail"
        assert card["verdict"] == "no_edge"


def test_paper_pending_below_threshold():
    with Storage(":memory:") as store:
        _seed_closed_picks(store, 5, beat=True)
        card = build_scorecard(store)
        assert card["paper"]["status"] == "pending"
        assert card["paper"]["closed"] == 5
        assert card["paper"]["progress"] < 1.0


def test_verdict_confirmed_when_both_pass():
    with Storage(":memory:") as store:
        _save_backtest(store, excess=0.10, hit=0.60)
        _seed_closed_picks(store, MIN_CLOSED_PICKS, beat=True)
        card = build_scorecard(store)
        assert card["backtest"]["status"] == "pass"
        assert card["paper"]["status"] == "pass"
        assert card["verdict"] == "confirmed"


def test_verdict_no_edge_when_paper_fails_even_if_backtest_passes():
    with Storage(":memory:") as store:
        _save_backtest(store, excess=0.10, hit=0.60)
        _seed_closed_picks(store, MIN_CLOSED_PICKS, beat=False)
        card = build_scorecard(store)
        assert card["backtest"]["status"] == "pass"
        assert card["paper"]["status"] == "fail"
        assert card["verdict"] == "no_edge"
