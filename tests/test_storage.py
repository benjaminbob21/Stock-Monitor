"""DuckDB storage tests (in-memory)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pandas as pd

from stock_monitor.storage import Storage


def _feature_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "AAA", "as_of": dt.date(2025, 1, 1),
             "fundamentals_known_on": dt.date(2024, 11, 1),
             "mom_12_1": 0.2, "mom_6_1": 0.1, "vol_3m": 0.3,
             "roe": 0.15, "debt_ratio": 0.4, "profit_margin": 0.2, "label": 1},
            {"ticker": "BBB", "as_of": dt.date(2025, 1, 1),
             "fundamentals_known_on": None,
             "mom_12_1": 0.1, "mom_6_1": 0.05, "vol_3m": 0.25,
             "roe": float("nan"), "debt_ratio": float("nan"),
             "profit_margin": float("nan"), "label": 0},
        ]
    )


def test_upsert_is_idempotent() -> None:
    df = _feature_frame()
    with Storage(":memory:") as store:
        store.upsert_features(df)
        store.upsert_features(df)  # replace, not duplicate
        assert store.count("features") == 2
        read = store.read_features()
        assert set(read["ticker"]) == {"AAA", "BBB"}
        assert read.set_index("ticker").loc["AAA", "label"] == 1


def test_insert_score_and_quarantine() -> None:
    with Storage(":memory:") as store:
        store.insert_score(
            ticker="AAA",
            as_of=dt.date(2025, 1, 1),
            conviction=82,
            recommendation="consider buying",
            model_version="v-test",
            fundamentals_known_on=dt.date(2024, 11, 1),
            drivers=[{"feature": "roe", "value": 0.15, "shap": 1.2}],
            risk_flags=["high_volatility"],
        )
        assert store.count("scores") == 1

        bad = _feature_frame().head(1).copy()
        bad["quarantine_reason"] = "vol_3m=-1.0"
        store.record_quarantine(bad)
        assert store.count("quarantine") == 1


def test_read_recent_scores_newest_per_ticker() -> None:
    with Storage(":memory:") as store:
        for conviction in (60, 82):
            store.insert_score(
                ticker="BLBD",
                as_of=dt.date.today(),
                conviction=conviction,
                recommendation="consider buying",
                model_version="v-test",
                fundamentals_known_on=None,
                drivers=[],
                risk_flags=[],
            )
        store.insert_score(
            ticker="OLD",
            as_of=dt.date.today() - dt.timedelta(days=10),
            conviction=95,
            recommendation="consider buying",
            model_version="v-test",
            fundamentals_known_on=None,
            drivers=[],
            risk_flags=[],
        )
        recent = store.read_recent_scores(within_days=3)
        assert len(recent) == 1
        assert recent[0]["ticker"] == "BLBD"
        assert recent[0]["conviction"] == 82  # newest score wins
        # A wider window picks up the stale lookup too.
        wide = store.read_recent_scores(within_days=30)
        assert {r["ticker"] for r in wide} == {"BLBD", "OLD"}


def test_older_features_table_is_migrated(tmp_path: Path) -> None:
    # Simulate a DB created by an earlier version (pre feature-expansion schema).
    path = str(tmp_path / "old.duckdb")
    con = duckdb.connect(path)
    con.execute(
        """
        CREATE TABLE features (
            ticker VARCHAR NOT NULL,
            as_of DATE NOT NULL,
            fundamentals_known_on DATE,
            mom_12_1 DOUBLE, mom_6_1 DOUBLE, vol_3m DOUBLE,
            roe DOUBLE, debt_ratio DOUBLE, profit_margin DOUBLE,
            label INTEGER,
            ingested_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY (ticker, as_of)
        )
        """
    )
    con.close()

    # Opening with the current code should add the new columns and accept inserts.
    with Storage(path) as store:
        store.upsert_features(_feature_frame())
        assert store.count("features") == 2
