"""Universe scan tests (network-free via fake providers)."""

from __future__ import annotations

from types import SimpleNamespace

from stock_monitor.scan import run_scan
from stock_monitor.service import apply_risk_caps, strong_recommendations
from stock_monitor.storage import Storage


def test_run_scan_ranks_and_persists(world: SimpleNamespace) -> None:
    with Storage(":memory:") as store:
        ranked = run_scan(
            ["AAA"],
            world.model,
            world.version,
            world.price_provider,
            world.fundamental_provider,
            storage=store,
        )
        assert ranked, "expected at least one scored ticker"
        assert ranked[0]["rank"] == 1
        assert 0 <= ranked[0]["capped_conviction"] <= 100
        assert ranked[0]["capped_conviction"] <= ranked[0]["conviction"]

        latest = store.read_latest_opportunities(limit=10)
        assert latest and latest[0]["ticker"] == "AAA"


def test_apply_risk_caps_penny_and_vol() -> None:
    row = {"vol_3m": 0.2, "fundamentals_known_on": None}
    # Penny price caps hard; also no fundamentals caps at 50.
    capped, caps = apply_risk_caps(90, row, price=2.0)
    assert capped <= 15
    assert "penny_stock_cap" in caps
    assert "no_fundamentals_cap" in caps


def test_apply_risk_caps_extreme_vol() -> None:
    row = {"vol_3m": 1.2, "fundamentals_known_on": "2025-01-01"}
    capped, caps = apply_risk_caps(88, row, price=100.0)
    assert capped <= 40
    assert "extreme_volatility_cap" in caps


def test_apply_risk_caps_noop_when_clean() -> None:
    row = {"vol_3m": 0.25, "fundamentals_known_on": "2025-01-01"}
    capped, caps = apply_risk_caps(72, row, price=150.0)
    assert capped == 72
    assert caps == []


def test_strong_recommendations_only_high_confidence_and_clean() -> None:
    opps = [
        {"ticker": "A", "capped_conviction": 85, "recommendation": "consider buying",
         "risk_flags": []},
        {"ticker": "B", "capped_conviction": 90, "recommendation": "consider buying",
         "risk_flags": ["no_fundamentals_cap"]},  # capped -> excluded
        {"ticker": "C", "capped_conviction": 60, "recommendation": "lean buy / watch",
         "risk_flags": []},  # below threshold
    ]
    strong = strong_recommendations(opps, threshold=80)
    assert [s["ticker"] for s in strong] == ["A"]
    assert strong[0]["rationale"]
