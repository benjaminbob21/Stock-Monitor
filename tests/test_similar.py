"""Similar-past-setups tests: nearest-neighbour base rate over the PIT feature store."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_monitor.features.builder import FEATURE_COLUMNS
from stock_monitor.similar import find_similar_setups


def _row(ticker: str, as_of: dt.date, base: float, label: int) -> dict:
    row = {c: base for c in FEATURE_COLUMNS}
    row["ticker"] = ticker
    row["as_of"] = as_of
    row["label"] = label
    return row


def _history() -> pd.DataFrame:
    rows = [
        # A tight cluster near 0.10 that mostly beat the benchmark (label 1).
        _row("AAA", dt.date(2020, 1, 1), 0.10, 1),
        _row("BBB", dt.date(2020, 2, 1), 0.11, 1),
        _row("CCC", dt.date(2020, 3, 1), 0.09, 1),
        _row("DDD", dt.date(2020, 4, 1), 0.12, 0),
        # A far-away cluster near 0.90 that mostly lost (label 0).
        _row("EEE", dt.date(2020, 5, 1), 0.90, 0),
        _row("FFF", dt.date(2020, 6, 1), 0.92, 0),
        _row("GGG", dt.date(2020, 7, 1), 0.88, 0),
    ]
    return pd.DataFrame(rows)


def test_similar_finds_nearest_cluster_and_base_rate() -> None:
    target = {c: 0.10 for c in FEATURE_COLUMNS}
    target["ticker"] = "NEW"
    target["as_of"] = dt.date(2024, 1, 1)

    result = find_similar_setups(target, _history(), k=3)
    tickers = {a["ticker"] for a in result["analogs"]}
    assert tickers <= {"AAA", "BBB", "CCC", "DDD"}  # only the near cluster
    assert result["base_rate"] == 1.0  # AAA/BBB/CCC all beat (label 1)
    assert result["n_history"] == 7
    assert 0.0 <= result["overall_base_rate"] <= 1.0


def test_similar_excludes_the_targets_own_row() -> None:
    hist = _history()
    target = {c: 0.10 for c in FEATURE_COLUMNS}
    target["ticker"] = "AAA"
    target["as_of"] = dt.date(2020, 1, 1)  # identical to a history row

    result = find_similar_setups(target, hist, k=3)
    self_match = [
        a for a in result["analogs"]
        if a["ticker"] == "AAA" and a["as_of"] == "2020-01-01"
    ]
    assert self_match == []


def test_similar_handles_empty_history() -> None:
    result = find_similar_setups({"ticker": "X", "as_of": dt.date(2024, 1, 1)}, pd.DataFrame())
    assert result["analogs"] == []
    assert result["base_rate"] is None
