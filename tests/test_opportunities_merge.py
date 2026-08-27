"""Tests for merging on-demand scores into the ranked opportunities list."""

from __future__ import annotations

from stock_monitor.api.app import _merge_on_demand_scores


def _scan_row(ticker: str, conviction: int) -> dict:
    return {
        "scan_ts": "2026-08-27T00:12:31",
        "rank": 0,
        "ticker": ticker,
        "conviction": conviction,
        "capped_conviction": conviction,
        "recommendation": "consider buying" if conviction >= 60 else "hold",
        "as_of": "2026-08-26",
        "risk_flags": [],
        "model_version": "v-test",
    }


def _demand_row(ticker: str, conviction: int) -> dict:
    return {
        "ticker": ticker,
        "as_of": "2026-08-26",
        "conviction": conviction,
        "recommendation": "consider buying",
        "risk_flags": [],
        "model_version": "v-test",
    }


def test_on_demand_tickers_are_merged_and_reranked() -> None:
    scan = [_scan_row("TSLA", 90), _scan_row("KO", 55)]
    recent = [_demand_row("BLBD", 82)]
    merged, added = _merge_on_demand_scores(scan, recent)
    assert added == 1
    assert [r["ticker"] for r in merged] == ["TSLA", "BLBD", "KO"]
    assert [r["rank"] for r in merged] == [1, 2, 3]
    assert merged[1]["source"] == "on_demand"
    assert merged[0]["source"] == "scan"


def test_scan_rows_win_over_duplicate_demand_rows() -> None:
    scan = [_scan_row("AAPL", 70)]
    recent = [_demand_row("AAPL", 95)]
    merged, added = _merge_on_demand_scores(scan, recent)
    assert added == 0
    assert len(merged) == 1
    assert merged[0]["conviction"] == 70


def test_no_recent_scores_leaves_list_untouched() -> None:
    scan = [_scan_row("MSFT", 88)]
    merged, added = _merge_on_demand_scores(scan, [])
    assert added == 0
    assert len(merged) == 1
    assert merged[0]["rank"] == 1
