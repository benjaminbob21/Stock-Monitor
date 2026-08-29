"""Tests for the DCF valuation engine and /dcf/{ticker} endpoint."""

from __future__ import annotations

import datetime as dt
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from stock_monitor.dcf import compute_dcf
from stock_monitor.providers.base import FundamentalFact


def _fact(
    concept: str,
    value: float,
    year: int,
    known_on: dt.date | None = None,
) -> FundamentalFact:
    return FundamentalFact(
        ticker="TEST",
        concept=concept,
        value=value,
        unit="USD",
        fiscal_end=dt.date(year, 12, 31),
        known_on=known_on or dt.date(year + 1, 2, 15),
        form="10-K",
    )


# OCF 1000→2000 over 4 years (~26% CAGR), capex 100/yr → FCF 900→1900.
def _healthy_facts() -> list[FundamentalFact]:
    facts: list[FundamentalFact] = []
    for i, year in enumerate(range(2021, 2025)):
        ocf = 1000.0 + 250.0 * i
        facts.append(_fact("NetCashProvidedByUsedInOperatingActivities", ocf, year))
        facts.append(_fact("PaymentsToAcquirePropertyPlantAndEquipment", 100.0, year))
    facts.append(_fact("CommonStockSharesOutstanding", 1000.0, 2024))
    facts.append(_fact("StockholdersEquity", 5000.0, 2024))
    facts.append(_fact("Liabilities", 2000.0, 2024))
    facts.append(_fact("CashAndCashEquivalentsAtCarryingValue", 500.0, 2024))
    return facts


# ---------- engine ----------


def test_dcf_positive_value_with_defaults() -> None:
    result = compute_dcf(_healthy_facts(), price=50.0, as_of=dt.date(2026, 8, 28))
    assert result["confidence"] == "good"
    assert result["value"] is not None and result["value"] > 0
    assert result["upside_pct"] is not None
    assert result["verdict"] in ("undervalued", "overvalued", "fairly valued")
    assert len(result["flows"]) == 5
    assert result["pv_terminal"] > result["pv_explicit"] > 0
    assert 0 < result["terminal_weight"] < 1


def test_dcf_growth_anchor_is_revenue_cagr() -> None:
    facts = [
        _fact("Revenues", 1000.0, 2021),
        _fact("Revenues", 2000.0, 2024),
        *_healthy_facts(),
    ]
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2026, 8, 28))
    assert result["inputs"]["growth_source"] == "revenue cagr"
    # 1000→2000 in 3 years ≈ 26% CAGR, clamped to 30% band → unchanged.
    assert result["inputs"]["growth_pct"] == pytest.approx(2 ** (1 / 3) - 1, abs=0.01)


def test_dcf_growth_clamped_to_sane_band() -> None:
    facts = [
        _fact("Revenues", 100.0, 2021),
        _fact("Revenues", 1000.0, 2024),  # ~99% CAGR
        *_healthy_facts(),
    ]
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2026, 8, 28))
    assert result["inputs"]["growth_pct"] == pytest.approx(0.30)
    assert any("clamped" in r for r in result["reasons"])


def test_dcf_negative_fcf_no_anchor_is_none() -> None:
    facts = [
        _fact("NetCashProvidedByUsedInOperatingActivities", -500.0, 2024),
        _fact("CommonStockSharesOutstanding", 1000.0, 2024),
    ]
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2026, 8, 28))
    assert result["value"] is None
    assert result["confidence"] == "none"
    assert result["reasons"]


def test_dcf_negative_fcf_with_manual_growth_still_values() -> None:
    facts = [
        _fact("NetCashProvidedByUsedInOperatingActivities", -500.0, 2024),
        _fact("CommonStockSharesOutstanding", 1000.0, 2024),
    ]
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2026, 8, 28), growth=0.15)
    assert result["value"] is not None


def test_dcf_capex_missing_is_rough_and_proxies_ocf() -> None:
    facts = [
        _fact("NetCashProvidedByUsedInOperatingActivities", 1500.0, 2024),
        _fact("CommonStockSharesOutstanding", 1000.0, 2024),
        _fact("StockholdersEquity", 5000.0, 2024),
        _fact("Liabilities", 2000.0, 2024),
    ]
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2026, 8, 28), growth=0.0)
    assert result["confidence"] == "rough"
    assert result["inputs"]["base_fcf"] == 1500.0
    assert any("capex not reported" in r for r in result["reasons"])


def test_dcf_net_debt_bridge_uses_liabilities_minus_cash() -> None:
    result = compute_dcf(_healthy_facts(), price=50.0, as_of=dt.date(2026, 8, 28))
    assert result["inputs"]["net_debt"] == 1500.0  # 2000 − 500
    assert result["inputs"]["bridge"] == "liabilities − cash"


def test_dcf_growth_anchor_prefers_freshest_revenue_alias() -> None:
    # MSFT/AAPL pattern: stale "Revenues" series ends years before the fresh
    # contract-revenue tag. The anchor must use the freshest series.
    facts = [
        _fact("Revenues", 1000.0, 2021),
        _fact("Revenues", 1100.0, 2022),  # stale: ends 2022
        _fact("RevenueFromContractWithCustomerExcludingAssessedTax", 1000.0, 2022),
        _fact("RevenueFromContractWithCustomerExcludingAssessedTax", 1400.0, 2024),
        *_healthy_facts(),
    ]
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2026, 8, 28))
    assert result["inputs"]["growth_source"] == "revenue cagr"
    # Fresh alias 1000→1400 in 2 years ≈ 18.3% CAGR — not the stale 4.9%.
    assert result["inputs"]["growth_pct"] == pytest.approx(1.4 ** 0.5 - 1, abs=0.01)


def test_dcf_bridge_prefers_filed_debt_over_liabilities() -> None:
    # Non-financial with filed debt: bridge must use debt − cash, not total
    # liabilities − cash (which would count payables/deferred revenue as debt).
    facts = [
        *_healthy_facts(),
        _fact("LongTermDebtNoncurrent", 800.0, 2024),
        _fact("LongTermDebtCurrent", 200.0, 2024),
    ]
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2026, 8, 28))
    assert result["inputs"]["net_debt"] == 500.0  # (800 + 200) − 500
    assert result["inputs"]["bridge"] == "filed debt − cash"


def test_dcf_no_share_count_is_none() -> None:
    facts = [f for f in _healthy_facts() if f.concept != "CommonStockSharesOutstanding"]
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2026, 8, 28))
    assert result["value"] is None
    assert result["confidence"] == "none"
    assert any("share count" in r for r in result["reasons"])


def test_dcf_pit_ignores_facts_filed_after_as_of() -> None:
    facts = _healthy_facts()
    shares = _fact("CommonStockSharesOutstanding", 1000.0, 2023)  # filed 2024-02-15
    equity = _fact("StockholdersEquity", 5000.0, 2023)
    liabilities = _fact("Liabilities", 2000.0, 2023)
    facts += [shares, equity, liabilities]
    # The 2024 fiscal-year facts are filed 2025-02-15 — not yet public on 2025-01-01.
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2025, 1, 1))
    assert result["inputs"]["fcf_years"] is not None
    assert result["inputs"]["fcf_years"].endswith("2023")
    # On the later date the 2024 filing IS known and the window extends.
    later = compute_dcf(facts, price=50.0, as_of=dt.date(2025, 6, 1))
    assert later["inputs"]["fcf_years"].endswith("2024")


def test_dcf_invalid_wacc_rejected() -> None:
    result = compute_dcf(_healthy_facts(), price=50.0, wacc=0.6)
    assert result["value"] is None
    assert result["confidence"] == "none"


def test_dcf_terminal_growth_must_be_below_wacc() -> None:
    result = compute_dcf(_healthy_facts(), price=50.0, wacc=0.03, terminal_growth=0.03)
    assert result["value"] is None


def test_dcf_no_price_still_returns_value_but_no_verdict() -> None:
    result = compute_dcf(_healthy_facts(), price=None, as_of=dt.date(2026, 8, 28))
    assert result["value"] is not None
    assert result["upside_pct"] is None
    assert result["verdict"] is None


def test_dcf_financial_liabilities_not_netted_as_debt() -> None:
    # A bank's total liabilities (deposits etc.) dwarf enterprise value; netting
    # them produced negative equity for every financial. Capex-less companies
    # fall back to filed borrowings instead.
    facts = [
        f for f in _healthy_facts() if f.concept != "PaymentsToAcquirePropertyPlantAndEquipment"
    ]
    facts.append(_fact("LongTermDebtNoncurrent", 400.0, 2024))
    result = compute_dcf(facts, price=50.0, as_of=dt.date(2026, 8, 28))
    assert result["confidence"] == "rough"
    assert result["inputs"]["bridge"].startswith("filed debt")
    assert result["inputs"]["net_debt"] == 400.0 - 500.0
    assert result["value"] is not None and result["value"] > 0


# ---------- endpoint ----------


class _FakePriceProvider:
    def get_quote(self, ticker: str) -> float | None:  # noqa: U100
        return None

    def get_prices(self, ticker: str, start: Any, end: Any) -> Any:  # noqa: ARG002, U100
        import pandas as pd

        return pd.DataFrame({"close": [50.0]}, index=[dt.date.today() - dt.timedelta(days=1)])


class _FakeFundamentals:
    def get_fundamentals(self, ticker: str, concepts: Any = None) -> list:  # noqa: ARG002, U100
        return _healthy_facts()


def test_dcf_endpoint_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_monitor.api.app import app, get_state

    monkeypatch.setattr(
        sys.modules["stock_monitor.api.app"],
        "get_settings",
        lambda: SimpleNamespace(
            api_shared_secret=None,
            run_scheduler=False,
            llm_analyst_enabled=False,
            openrouter_api_key="",
            llm_model="test/model",
            llm_base_url="https://llm.test/api/v1",
        ),
    )
    state = SimpleNamespace(
        model=object(),
        model_version="t",
        price_provider=_FakePriceProvider(),
        fundamental_provider=_FakeFundamentals(),
        db_path=None,
        label_window_months=6,
    )
    app.dependency_overrides[get_state] = lambda: state
    try:
        client = TestClient(app)
        resp = client.get("/dcf/TEST")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ticker"] == "TEST"
        assert body["value"] > 0
        assert body["price"] == 50.0
        assert body["confidence"] == "good"
    finally:
        app.dependency_overrides.pop(get_state, None)


def test_dcf_endpoint_passes_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_monitor.api.app import app, get_state

    monkeypatch.setattr(
        sys.modules["stock_monitor.api.app"],
        "get_settings",
        lambda: SimpleNamespace(
            api_shared_secret=None,
            run_scheduler=False,
            llm_analyst_enabled=False,
            openrouter_api_key="",
            llm_model="test/model",
            llm_base_url="https://llm.test/api/v1",
        ),
    )
    state = SimpleNamespace(
        model=object(),
        model_version="t",
        price_provider=_FakePriceProvider(),
        fundamental_provider=_FakeFundamentals(),
        db_path=None,
        label_window_months=6,
    )
    app.dependency_overrides[get_state] = lambda: state
    try:
        client = TestClient(app)
        resp = client.get("/dcf/TEST?growth=0.05&wacc=0.10")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["inputs"]["growth_pct"] == pytest.approx(0.05)
        assert body["inputs"]["wacc_pct"] == pytest.approx(0.10)
    finally:
        app.dependency_overrides.pop(get_state, None)


def test_dcf_endpoint_no_fundamentals_is_none_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stock_monitor.api.app import app, get_state

    monkeypatch.setattr(
        sys.modules["stock_monitor.api.app"],
        "get_settings",
        lambda: SimpleNamespace(
            api_shared_secret=None,
            run_scheduler=False,
            llm_analyst_enabled=False,
            openrouter_api_key="",
            llm_model="test/model",
            llm_base_url="https://llm.test/api/v1",
        ),
    )
    state = SimpleNamespace(
        model=object(),
        model_version="t",
        price_provider=_FakePriceProvider(),
        fundamental_provider=_FakeFundamentals.__new__(_FakeFundamentals),
        db_path=None,
        label_window_months=6,
    )
    state.fundamental_provider.get_fundamentals = lambda ticker, concepts=None: []
    app.dependency_overrides[get_state] = lambda: state
    try:
        client = TestClient(app)
        resp = client.get("/dcf/TEST")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["value"] is None
        assert body["confidence"] == "none"
        assert body["reasons"]
    finally:
        app.dependency_overrides.pop(get_state, None)
