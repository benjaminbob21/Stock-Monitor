"""Tests for the LLM brief layer (brief narration + per-stock review)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from stock_monitor import brief as brief_mod
from stock_monitor.allocation.contracts import (
    AllocationConstraints,
    AllocationPlan,
    PositionAllocation,
)


def _plan(total: float = 10_000.0) -> AllocationPlan:
    allocs = (
        PositionAllocation(
            ticker="AAPL", target_weight=0.60, current_weight=0.50,
            conviction=72.0, reasons=["conviction 72"],
        ),
        PositionAllocation(
            ticker="MSFT", target_weight=0.30, current_weight=0.50,
            conviction=65.0, reasons=["conviction 65"],
        ),
    )
    return AllocationPlan(
        as_of=dt.datetime(2026, 8, 28, 12, 0),
        total_value=total,
        allocations=allocs,
        cash_weight=0.10,
        warnings=["one or more positions hit the per-position cap"],
        constraints=AllocationConstraints(),
    )


class _FakePrices:
    def get_quote(self, ticker: str) -> dict[str, float] | None:  # noqa: U100
        return {"AAPL": 5000.0, "MSFT": 5000.0}.get(ticker)  # type: ignore[return-value]

    def get_prices(self, ticker: str, start: Any, end: Any) -> list[dict]:  # noqa: ARG002, U100
        return []


class _FakeSettings:
    llm_analyst_enabled = True
    openrouter_api_key = "test-key"
    llm_model = "test/model"
    llm_base_url = "https://llm.test/api/v1"


class _OffSettings:
    llm_analyst_enabled = False
    openrouter_api_key = ""
    llm_model = "test/model"
    llm_base_url = "https://llm.test/api/v1"


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    brief_mod._brief_cache.clear()
    brief_mod._review_cache.clear()
    yield
    brief_mod._brief_cache.clear()
    brief_mod._review_cache.clear()


class _FakeStore:
    """Minimal store for build_allocation_plan: one open position + one score."""

    def read_features(self) -> Any:
        import pandas as pd

        return pd.DataFrame(
            {
                "ticker": ["AAPL", "MSFT"],
                "as_of": [dt.date(2026, 8, 27)] * 2,
                "vol_3m": [0.30, 0.20],
            }
        )

    def read_news_sentiment(self, ticker: str | None = None) -> Any:
        import pandas as pd

        return pd.DataFrame(columns=["ticker", "as_of", "sentiment", "article_count"])

    def read_alt_sentiment(self, ticker: str | None = None) -> Any:
        import pandas as pd

        return pd.DataFrame(columns=["ticker", "as_of", "sentiment", "buzz", "summary"])

    def read_recent_scores(self, within_days: int = 3) -> list[dict]:
        return [
            {
                "ticker": "AAPL", "as_of": "2026-08-27", "conviction": 72.0,
                "recommendation": "buy", "risk_flags": [], "model_version": "t",
                "drivers": [],
            },
            {
                "ticker": "MSFT", "as_of": "2026-08-27", "conviction": 65.0,
                "recommendation": "buy", "risk_flags": [], "model_version": "t",
                "drivers": [],
            },
        ]

    def list_positions(self, status: str | None = None) -> list[dict]:
        return [
            {
                "id": "p1", "ticker": "AAPL", "added_at": "2026-08-01T00:00:00",
                "entry_price": 100.0, "entry_conviction": 70.0,
                "entry_recommendation": "buy", "entry_drivers": [], "status": "open",
                "sold_at": None, "sold_price": None,
            }
        ]


# ---------- context builder (deterministic, no LLM) ----------


def test_brief_context_carries_engine_numbers_only() -> None:
    from stock_monitor.brief import build_brief_context

    ctx = build_brief_context(_FakeStore(), _FakePrices(), total_value=10_000.0)
    assert ctx["total_value"] == 10_000.0
    # Both names hit the default 15% cap → the rest sits in cash.
    invested = sum(a["target_pct"] for a in ctx["allocations"])
    assert ctx["cash_pct"] == pytest.approx(100.0 - invested, abs=0.2)
    assert ctx["cash_pct"] > 0
    tickers = {a["ticker"] for a in ctx["allocations"]}
    assert {"AAPL", "MSFT"} <= tickers
    for a in ctx["allocations"]:
        assert set(a) == {
            "ticker", "target_pct", "current_pct", "delta_pct", "conviction", "reasons"
        }


# ---------- portfolio_brief ----------


def test_portfolio_brief_disabled_returns_context_without_brief() -> None:
    from stock_monitor.brief import portfolio_brief

    result = portfolio_brief(
        _FakeStore(), _FakePrices(), total_value=10_000.0, settings=_OffSettings()
    )
    assert result["brief"] is None
    assert result["llm_available"] is False
    assert "LLM_ANALYST_ENABLED" in (result["note"] or "")
    assert result["context"]["allocations"]  # deterministic plan still present


def test_portfolio_brief_narrates_and_caches_per_day() -> None:
    from stock_monitor.brief import portfolio_brief

    with patch.object(
        brief_mod, "_narrate_brief", return_value="Engine suggests trimming AAPL."
    ) as narr:
        first = portfolio_brief(
            _FakeStore(), _FakePrices(), total_value=10_000.0, settings=_FakeSettings()
        )
        second = portfolio_brief(
            _FakeStore(), _FakePrices(), total_value=10_000.0, settings=_FakeSettings()
        )
    assert first["brief"] == "Engine suggests trimming AAPL."
    assert first["cached"] is False
    assert second["brief"] == "Engine suggests trimming AAPL."
    assert second["cached"] is True
    assert narr.call_count == 1  # one LLM call per day, second hit is cache


def test_portfolio_brief_llm_failure_still_returns_plan() -> None:
    from stock_monitor.brief import portfolio_brief
    with patch.object(brief_mod, "_narrate_brief", return_value=None):
        result = portfolio_brief(
            _FakeStore(), _FakePrices(), total_value=10_000.0, settings=_FakeSettings()
        )
    assert result["brief"] is None
    assert result["context"]["allocations"]


# ---------- ticker_review ----------


def test_ticker_review_disabled_returns_none() -> None:
    from stock_monitor.brief import ticker_review

    assert ticker_review({"ticker": "AAPL"}, _OffSettings()) is None


def test_ticker_review_parses_and_caches_one_hour() -> None:
    from stock_monitor.brief import ticker_review

    payload = {"ticker": "AAPL", "conviction": 72, "recommendation": "buy", "risk_flags": []}
    content = (
        '{"opinion": "HOLD", "confidence": "medium", '
        '"rationale": "Solid but priced in.", "key_risks": ["valuation"]}'
    )
    with patch("requests.post") as post:
        post.return_value = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": content}}]},
        )
        first = ticker_review(payload, _FakeSettings())
        second = ticker_review(payload, _FakeSettings())
    assert first is not None and first["opinion"] == "HOLD"
    assert first["cached"] is False
    assert second is not None and second["cached"] is True
    assert post.call_count == 1  # 1/hour cache


def test_ticker_review_invalid_opinion_rejected() -> None:
    from stock_monitor.brief import ticker_review

    with patch("requests.post") as post:
        post.return_value = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": '{"opinion": "MAYBE"}'}}]},
        )
        assert ticker_review({"ticker": "AAPL"}, _FakeSettings()) is None


# ---------- endpoints ----------


def test_brief_endpoint_disabled_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Disabled LLM: brief is None, note explains, deterministic plan still returned."""
    import os
    import sys
    import tempfile

    from stock_monitor.api.app import app, get_state

    # Force the disabled branch regardless of the local .env flags. The
    # endpoint resolves get_settings as a module global — patch it there.
    monkeypatch.setattr(
        sys.modules["stock_monitor.api.app"],
        "get_settings",
        lambda: SimpleNamespace(
            api_shared_secret=None, run_scheduler=False, llm_analyst_enabled=False,
            openrouter_api_key="", llm_model="test/model",
            llm_base_url="https://llm.test/api/v1",
        ),
    )
    db = os.path.join(tempfile.mkdtemp(dir=str(tmp_path)), "b.duckdb")
    state = SimpleNamespace(
        model=object(), model_version="t", price_provider=_FakePrices(),
        fundamental_provider=None, db_path=db, label_window_months=6,
    )
    app.dependency_overrides[get_state] = lambda: state
    try:
        client = TestClient(app)
        resp = client.get("/brief")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["brief"] is None
        assert body["llm_available"] is False
        assert body["note"] and "LLM_ANALYST_ENABLED" in body["note"]
        assert isinstance(body["context"]["allocations"], list)
    finally:
        app.dependency_overrides.pop(get_state, None)


def test_review_endpoint_404_without_recent_score(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A ticker with no recent score in the (empty) DB → 404, not a crash."""

    import os
    import sys
    import tempfile

    from stock_monitor.api.app import app, get_state

    monkeypatch.setattr(
        sys.modules["stock_monitor.api.app"],
        "get_settings",
        lambda: SimpleNamespace(
            api_shared_secret=None, run_scheduler=False, llm_analyst_enabled=False,
            openrouter_api_key="", llm_model="test/model",
            llm_base_url="https://llm.test/api/v1",
        ),
    )
    db = os.path.join(tempfile.mkdtemp(dir=str(tmp_path)), "b.duckdb")
    # LLM enabled so the endpoint reaches the score lookup.
    settings = SimpleNamespace(
        api_shared_secret=None, run_scheduler=False, llm_analyst_enabled=True,
        openrouter_api_key="test-key", llm_model="test/model",
        llm_base_url="https://llm.test/api/v1",
    )
    monkeypatch.setattr(sys.modules["stock_monitor.api.app"], "get_settings", lambda: settings)
    state = SimpleNamespace(
        model=object(), model_version="t", price_provider=_FakePrices(),
        fundamental_provider=None, db_path=db, label_window_months=6,
    )
    app.dependency_overrides[get_state] = lambda: state
    try:
        client = TestClient(app)
        resp = client.post("/review/ZZZZ")
        assert resp.status_code == 404, resp.text
    finally:
        app.dependency_overrides.pop(get_state, None)
