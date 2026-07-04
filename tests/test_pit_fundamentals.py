"""PIT-correctness tests for fundamental selection — the anti-look-ahead guarantee."""

from __future__ import annotations

import datetime as dt

from stock_monitor.features.builder import latest_fact
from stock_monitor.providers.base import FundamentalFact


def _fact(concept: str, value: float, fiscal_end: str, known_on: str) -> FundamentalFact:
    return FundamentalFact(
        ticker="TEST",
        concept=concept,
        value=value,
        unit="USD",
        fiscal_end=dt.date.fromisoformat(fiscal_end),
        known_on=dt.date.fromisoformat(known_on),
        form="10-K",
    )


def test_latest_fact_ignores_facts_not_yet_filed() -> None:
    facts = [
        _fact("NetIncomeLoss", 100.0, "2023-12-31", "2024-02-15"),
        _fact("NetIncomeLoss", 200.0, "2024-12-31", "2025-02-15"),  # filed later
    ]
    # As of mid-2024, only the 2023 filing was knowable.
    picked = latest_fact(facts, "NetIncomeLoss", dt.date(2024, 6, 1))
    assert picked is not None
    assert picked.value == 100.0


def test_latest_fact_picks_most_recent_period_once_known() -> None:
    facts = [
        _fact("NetIncomeLoss", 100.0, "2023-12-31", "2024-02-15"),
        _fact("NetIncomeLoss", 200.0, "2024-12-31", "2025-02-15"),
    ]
    picked = latest_fact(facts, "NetIncomeLoss", dt.date(2025, 6, 1))
    assert picked is not None
    assert picked.value == 200.0


def test_latest_fact_returns_none_when_nothing_known_yet() -> None:
    facts = [_fact("Assets", 500.0, "2024-12-31", "2025-02-15")]
    assert latest_fact(facts, "Assets", dt.date(2025, 1, 1)) is None


def test_latest_fact_filters_by_concept() -> None:
    facts = [
        _fact("Assets", 500.0, "2023-12-31", "2024-02-15"),
        _fact("Liabilities", 300.0, "2023-12-31", "2024-02-15"),
    ]
    picked = latest_fact(facts, "Liabilities", dt.date(2024, 6, 1))
    assert picked is not None
    assert picked.value == 300.0
