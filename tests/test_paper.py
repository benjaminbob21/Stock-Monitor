"""Paper-mode tests: record simulated buys, score them vs SPY at maturity, summarise."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_monitor.config import Settings
from stock_monitor.paper import (
    compose_digest,
    evaluate_paper_picks,
    paper_summary,
    record_paper_picks,
)
from stock_monitor.providers.base import PRICE_COLUMNS
from stock_monitor.storage import Storage
from tests.conftest import FakePriceProvider


def _step_frame(before: float, after: float, switch: dt.date) -> pd.DataFrame:
    """A daily frame whose close is ``before`` up to ``switch`` and ``after`` from then on."""
    idx = pd.bdate_range("2023-12-01", "2025-03-01")
    close = [before if d.date() <= switch else after for d in idx]
    frame = pd.DataFrame(
        {c: (close if c != "volume" else [1_000_000] * len(idx)) for c in PRICE_COLUMNS},
        index=idx,
    )
    frame.index.name = "date"
    return frame


def _provider() -> FakePriceProvider:
    switch = dt.date(2024, 6, 30)
    # AAA: 100 -> 130 (+30%). SPY: 400 -> 440 (+10%). Excess = +20% -> beats.
    return FakePriceProvider(
        {
            "AAA": _step_frame(100.0, 130.0, switch),
            "SPY": _step_frame(400.0, 440.0, switch),
        }
    )


def _settings() -> Settings:
    return Settings(
        db_path=":memory:", paper_min_conviction=70, paper_horizon_months=12,
        telegram_bot_token="", smtp_host="",
    )


def _ranked() -> list[dict]:
    return [
        {"ticker": "AAA", "rank": 1, "capped_conviction": 85, "conviction": 88,
         "recommendation": "consider buying", "risk_flags": [], "model_version": "v1"},
        {"ticker": "LOW", "rank": 2, "capped_conviction": 40, "conviction": 40,
         "recommendation": "watch", "risk_flags": [], "model_version": "v1"},
    ]


def test_record_paper_picks_is_idempotent() -> None:
    settings, provider = _settings(), _provider()
    pick_day = dt.date(2024, 1, 15)
    with Storage(":memory:") as store:
        recorded = record_paper_picks(settings, _ranked(), provider, store, today=pick_day)
        assert recorded == 1  # only the buy-zone name (AAA), not LOW
        # Re-running the same day must not duplicate.
        assert record_paper_picks(settings, _ranked(), provider, store, today=pick_day) == 0

        picks = store.list_paper_picks()
        assert len(picks) == 1
        assert picks[0]["ticker"] == "AAA"
        assert picks[0]["entry_price"] == 100.0
        assert picks[0]["benchmark_entry"] == 400.0
        assert picks[0]["matured_on"] == "2025-01-15"
        assert picks[0]["status"] == "open"


def test_evaluate_closes_only_matured_picks() -> None:
    settings, provider = _settings(), _provider()
    pick_day = dt.date(2024, 1, 15)
    with Storage(":memory:") as store:
        record_paper_picks(settings, _ranked(), provider, store, today=pick_day)

        # Before maturity: nothing closes.
        assert evaluate_paper_picks(settings, provider, store, today=dt.date(2024, 6, 1)) == 0
        assert store.list_paper_picks(status="open")

        # After maturity: closes with realised excess vs SPY.
        closed = evaluate_paper_picks(settings, provider, store, today=dt.date(2025, 2, 1))
        assert closed == 1
        pick = store.list_paper_picks(status="closed")[0]
        assert pick["exit_price"] == 130.0
        assert pick["benchmark_exit"] == 440.0
        assert round(pick["stock_return"], 4) == 0.3
        assert round(pick["benchmark_return"], 4) == 0.1
        assert round(pick["excess_return"], 4) == 0.2
        assert pick["beat_benchmark"] is True


def test_paper_summary_reports_hit_rate() -> None:
    settings, provider = _settings(), _provider()
    with Storage(":memory:") as store:
        record_paper_picks(settings, _ranked(), provider, store, today=dt.date(2024, 1, 15))
        evaluate_paper_picks(settings, provider, store, today=dt.date(2025, 2, 1))

        summary = paper_summary(store)
        assert summary["total_picks"] == 1
        assert summary["closed"] == 1
        assert summary["open"] == 0
        assert summary["hit_rate"] == 1.0
        assert summary["avg_excess_return"] == 0.2
        assert summary["best"]["ticker"] == "AAA"


def test_compose_digest_includes_names_and_record() -> None:
    ranked = _ranked()
    summary = {
        "closed": 3, "open": 2, "hit_rate": 0.667, "avg_excess_return": 0.05,
    }
    title, body = compose_digest(ranked, summary, top_n=5)
    assert "top 2 names" in title
    assert "AAA" in body
    assert "Paper track record" in body
    assert "67%" in body
    assert "No auto-trading" in body
