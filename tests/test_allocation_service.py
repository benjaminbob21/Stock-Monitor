"""Tests for the allocation service bridge + /allocation endpoint."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from stock_monitor.api.app import AppState, app, get_state
from stock_monitor.storage.db import Storage


class _FakePrices:
    """Price provider returning a flat last close per ticker."""

    def __init__(self, closes: dict[str, float]) -> None:
        self.closes = closes

    def get_quote(self, ticker: str) -> float | None:
        return self.closes.get(ticker.upper())

    def get_prices(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        close = self.closes.get(ticker.upper())
        if close is None:
            return pd.DataFrame()
        idx = pd.bdate_range(end=end, periods=5)
        return pd.DataFrame({"close": [close] * 5}, index=idx)


def _seed(store: Storage) -> None:
    # Two open positions with fresh scores; one unpriced position.
    store.insert_score(
        ticker="AAA", as_of=dt.date.today(), conviction=78, recommendation="buy",
        model_version="test", fundamentals_known_on=None, drivers=[], risk_flags=[],
    )
    store.insert_score(
        ticker="BBB", as_of=dt.date.today(), conviction=62, recommendation="buy",
        model_version="test", fundamentals_known_on=None, drivers=[],
        risk_flags=["high_volatility"],
    )
    store.insert_score(
        ticker="CCC", as_of=dt.date.today(), conviction=30, recommendation="sell",
        model_version="test", fundamentals_known_on=None, drivers=[], risk_flags=[],
    )
    store.add_position("pos-1", "AAA", dt.datetime.now(), 100.0, 70, "buy", [])
    store.add_position("pos-2", "BBB", dt.datetime.now(), 50.0, 60, "buy", [])
    # features with vol_3m
    frame = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "as_of": [dt.date.today(), dt.date.today()],
            "vol_3m": [0.25, 0.40],
        }
    )
    store.upsert_features(frame)
    # alt sentiment for AAA (fresh, biweekly LLM verdict)
    store.upsert_alt_sentiment_llm(
        [{"ticker": "AAA", "date": dt.date.today(), "sentiment": 0.5,
          "buzz": 8, "summary": "loved", "backend": "llm:test"}]
    )


def test_build_allocation_plan_maps_inputs_and_weights() -> None:
    with Storage() as store:
        _seed(store)
        from stock_monitor.allocation.service import build_allocation_plan

        plan, diag = build_allocation_plan(store, _FakePrices({"AAA": 110.0, "BBB": 55.0}))
    tickers = [a.ticker for a in plan.allocations]
    assert "AAA" in tickers and "BBB" in tickers
    assert all(a.ticker != "CCC" for a in plan.allocations)  # below sell band
    assert plan.cash_weight >= 0.10 - 1e-9  # cash floor respected at default
    total = plan.cash_weight + sum(a.target_weight for a in plan.allocations)
    assert abs(total - 1.0) < 1e-6  # plan sums to exactly 1
    assert diag["candidate_count"] == 2
    assert diag["book_value"] == pytest.approx(110.0 + 55.0)


def test_plan_json_shape_is_ui_friendly() -> None:
    with Storage() as store:
        _seed(store)
        from stock_monitor.allocation.service import build_allocation_plan, plan_to_json

        plan, diag = build_allocation_plan(
            store, _FakePrices({"AAA": 110.0, "BBB": 55.0}), total_value=10_000.0
        )
        body = plan_to_json(plan, diag)
    assert body["total_value"] == 10_000.0
    alloc = body["allocations"][0]
    assert {"ticker", "target_pct", "current_pct", "delta_pct", "conviction", "reasons"} <= set(
        alloc
    )
    assert 0 <= alloc["target_pct"] <= 100
    assert body["cash_pct"] >= 0


def test_unpriced_position_is_reported_not_fatal() -> None:
    with Storage() as store:
        _seed(store)
        from stock_monitor.allocation.service import build_allocation_plan

        _, diag = build_allocation_plan(store, _FakePrices({"AAA": 110.0}))
    assert diag["price_errors"] == {"BBB": "no price"}


def test_allocation_endpoint_smoke(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import sys
    import tempfile
    # Local .env sets API_SHARED_SECRET; neutralize for this endpoint test.
    app_module = sys.modules["stock_monitor.api.app"]
    monkeypatch.setattr(
        app_module,
        "get_settings",
        lambda: SimpleNamespace(api_shared_secret=None, run_scheduler=False),
    )
    tmp = os.path.join(tempfile.mkdtemp(), "alloc.duckdb")
    with Storage(tmp) as store:
        _seed(store)
    state = AppState(
        model=world.model,
        model_version=world.version,
        price_provider=_FakePrices({"AAA": 110.0, "BBB": 55.0}),
        fundamental_provider=world.fundamental_provider,
        db_path=tmp,
        label_window_months=12,
    )
    app.dependency_overrides[get_state] = lambda: state
    try:
        client = TestClient(app)
        resp = client.get("/allocation", params={"budget": 5000})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_value"] == 5000.0
        assert 0 <= body["cash_pct"] <= 100
        assert abs(body["cash_pct"] + sum(a["target_pct"] for a in body["allocations"]) - 100) < 0.1
    finally:
        app.dependency_overrides.clear()


def test_restricted_plan_splits_only_chosen_tickers() -> None:
    with Storage() as store:
        _seed(store)
        from stock_monitor.allocation.service import build_allocation_plan

        plan, diag = build_allocation_plan(
            store,
            _FakePrices({"AAA": 110.0}),
            total_value=1000.0,
            restrict_tickers=["AAA", "MMM"],
        )
    tickers = [a.ticker for a in plan.allocations]
    assert tickers == ["AAA", "MMM"]
    assert diag["unscored"] == ["MMM"]  # MMM had no score → neutral placeholder
    assert diag["restricted"] is True
    total = plan.cash_weight + sum(a.target_weight for a in plan.allocations)
    assert abs(total - 1.0) < 1e-6


def test_restricted_endpoint_smoke(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import sys
    import tempfile
    app_module = sys.modules["stock_monitor.api.app"]
    monkeypatch.setattr(
        app_module,
        "get_settings",
        lambda: SimpleNamespace(api_shared_secret=None, run_scheduler=False),
    )
    tmp = os.path.join(tempfile.mkdtemp(), "alloc2.duckdb")
    with Storage(tmp) as store:
        _seed(store)
    state = AppState(
        model=world.model,
        model_version=world.version,
        price_provider=_FakePrices({"AAA": 110.0, "MMM": 42.0}),
        fundamental_provider=world.fundamental_provider,
        db_path=tmp,
        label_window_months=12,
    )
    app.dependency_overrides[get_state] = lambda: state
    try:
        client = TestClient(app)
        resp = client.get("/allocation", params={"budget": 2500, "tickers": "AAA,MMM"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {a["ticker"] for a in body["allocations"]} == {"AAA", "MMM"}
        assert body["diagnostics"]["unscored"] == ["MMM"]
    finally:
        app.dependency_overrides.clear()
