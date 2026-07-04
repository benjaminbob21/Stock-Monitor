"""DuckDB analytical store (build-plan §4: DuckDB before Postgres).

Persists three things in a single embedded file (no server needed):
- ``features``   : PIT feature rows (ticker x as_of), one row per as-of date.
- ``scores``     : every conviction score emitted, with its drivers and risk flags.
- ``quarantine`` : rows rejected by the data-quality gate, with the reason.

Everything is idempotent: re-ingesting the same (ticker, as_of) replaces the row,
so the pipeline can be re-run safely.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import TracebackType

import duckdb
import pandas as pd

from stock_monitor.features.builder import FEATURE_COLUMNS

_FEATURE_INSERT_COLUMNS = (
    "ticker",
    "as_of",
    "fundamentals_known_on",
    *FEATURE_COLUMNS,
    "label",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    ticker VARCHAR NOT NULL,
    as_of DATE NOT NULL,
    fundamentals_known_on DATE,
    mom_12_1 DOUBLE,
    mom_6_1 DOUBLE,
    vol_3m DOUBLE,
    rsi_14 DOUBLE,
    trend_200 DOUBLE,
    roe DOUBLE,
    debt_ratio DOUBLE,
    profit_margin DOUBLE,
    earnings_yield DOUBLE,
    fcf_yield DOUBLE,
    sentiment DOUBLE,
    label INTEGER,
    ingested_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS scores (
    ticker VARCHAR NOT NULL,
    as_of DATE,
    scored_at TIMESTAMP DEFAULT now(),
    conviction INTEGER,
    recommendation VARCHAR,
    model_version VARCHAR,
    fundamentals_known_on DATE,
    drivers VARCHAR,
    risk_flags VARCHAR
);

CREATE TABLE IF NOT EXISTS quarantine (
    ticker VARCHAR,
    as_of DATE,
    reason VARCHAR,
    quarantined_at TIMESTAMP DEFAULT now()
);
"""


class Storage:
    """Thin, typed wrapper around a DuckDB connection."""

    def __init__(self, db_path: str = ":memory:") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(db_path)
        self._con.execute(_SCHEMA)
        self._migrate_feature_columns()

    def _migrate_feature_columns(self) -> None:
        """Forward-migrate the features table as FEATURE_COLUMNS grows across phases.

        ``CREATE TABLE IF NOT EXISTS`` won't add new columns to a pre-existing table,
        so an older DB would break inserts. Add any missing feature columns in place
        (no data loss) — the features table is append/upsert-only.
        """
        for column in FEATURE_COLUMNS:
            self._con.execute(
                f"ALTER TABLE features ADD COLUMN IF NOT EXISTS {column} DOUBLE"
            )

    def __enter__(self) -> Storage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._con.close()

    def upsert_features(self, df: pd.DataFrame) -> int:
        """Insert-or-replace feature rows keyed by (ticker, as_of)."""
        if df.empty:
            return 0
        sub = df.reindex(columns=list(_FEATURE_INSERT_COLUMNS)).copy()
        if "label" in sub:
            sub["label"] = sub["label"].astype("Int64")
        self._con.register("_incoming_features", sub)
        try:
            self._con.execute(
                """
                INSERT OR REPLACE INTO features
                    (ticker, as_of, fundamentals_known_on, mom_12_1, mom_6_1,
                     vol_3m, rsi_14, trend_200, roe, debt_ratio, profit_margin,
                     earnings_yield, fcf_yield, sentiment, label, ingested_at)
                SELECT ticker, as_of, fundamentals_known_on, mom_12_1, mom_6_1,
                       vol_3m, rsi_14, trend_200, roe, debt_ratio, profit_margin,
                       earnings_yield, fcf_yield, sentiment, label, now()
                FROM _incoming_features
                """
            )
        finally:
            self._con.unregister("_incoming_features")
        return len(sub)

    def record_quarantine(self, df: pd.DataFrame) -> int:
        """Persist rejected rows (expects a ``quarantine_reason`` column)."""
        if df.empty:
            return 0
        for row in df.itertuples(index=False):
            self._con.execute(
                "INSERT INTO quarantine (ticker, as_of, reason) VALUES (?, ?, ?)",
                [
                    getattr(row, "ticker", None),
                    getattr(row, "as_of", None),
                    getattr(row, "quarantine_reason", "schema"),
                ],
            )
        return len(df)

    def insert_score(
        self,
        *,
        ticker: str,
        as_of: dt.date | None,
        conviction: int,
        recommendation: str,
        model_version: str,
        fundamentals_known_on: dt.date | None,
        drivers: list[dict],
        risk_flags: list[str],
    ) -> None:
        """Record a single emitted score with its explanation and risk flags."""
        self._con.execute(
            """
            INSERT INTO scores
                (ticker, as_of, conviction, recommendation, model_version,
                 fundamentals_known_on, drivers, risk_flags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ticker,
                as_of,
                conviction,
                recommendation,
                model_version,
                fundamentals_known_on,
                json.dumps(drivers),
                json.dumps(risk_flags),
            ],
        )

    def count(self, table: str) -> int:
        """Return the row count of one of the known tables."""
        if table not in {"features", "scores", "quarantine"}:
            raise ValueError(f"unknown table: {table}")
        result = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()
        return int(result[0]) if result else 0

    def read_features(self) -> pd.DataFrame:
        """Return all stored feature rows as a DataFrame."""
        return self._con.execute("SELECT * FROM features ORDER BY ticker, as_of").df()
