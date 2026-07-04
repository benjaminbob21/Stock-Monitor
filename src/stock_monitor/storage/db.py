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

CREATE TABLE IF NOT EXISTS opportunities (
    scan_ts TIMESTAMP,
    rank INTEGER,
    ticker VARCHAR,
    conviction INTEGER,
    capped_conviction INTEGER,
    recommendation VARCHAR,
    as_of DATE,
    risk_flags VARCHAR,
    model_version VARCHAR
);

CREATE TABLE IF NOT EXISTS runs (
    job VARCHAR,
    status VARCHAR,
    detail VARCHAR,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    id VARCHAR PRIMARY KEY,
    ticker VARCHAR,
    added_at TIMESTAMP,
    entry_price DOUBLE,
    entry_conviction INTEGER,
    entry_recommendation VARCHAR,
    entry_drivers VARCHAR,
    status VARCHAR DEFAULT 'open',
    sold_at TIMESTAMP,
    sold_price DOUBLE
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
        if table not in {"features", "scores", "quarantine", "opportunities", "runs", "positions"}:
            raise ValueError(f"unknown table: {table}")
        result = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()
        return int(result[0]) if result else 0

    def add_position(
        self,
        position_id: str,
        ticker: str,
        added_at: dt.datetime,
        entry_price: float,
        entry_conviction: int,
        entry_recommendation: str,
        entry_drivers: list[dict],
    ) -> None:
        """Record a new tracked position (a buy the user made)."""
        self._con.execute(
            """
            INSERT INTO positions
                (id, ticker, added_at, entry_price, entry_conviction,
                 entry_recommendation, entry_drivers, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            [
                position_id,
                ticker,
                added_at,
                entry_price,
                entry_conviction,
                entry_recommendation,
                json.dumps(entry_drivers),
            ],
        )

    def _position_row(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "ticker": row[1],
            "added_at": row[2].isoformat() if row[2] is not None else None,
            "entry_price": row[3],
            "entry_conviction": row[4],
            "entry_recommendation": row[5],
            "entry_drivers": json.loads(row[6]) if row[6] else [],
            "status": row[7],
            "sold_at": row[8].isoformat() if row[8] is not None else None,
            "sold_price": row[9],
        }

    def list_positions(self) -> list[dict]:
        """Return all tracked positions (open and sold), newest first."""
        rows = self._con.execute(
            """
            SELECT id, ticker, added_at, entry_price, entry_conviction,
                   entry_recommendation, entry_drivers, status, sold_at, sold_price
            FROM positions ORDER BY added_at DESC
            """
        ).fetchall()
        return [self._position_row(r) for r in rows]

    def get_position(self, position_id: str) -> dict | None:
        row = self._con.execute(
            """
            SELECT id, ticker, added_at, entry_price, entry_conviction,
                   entry_recommendation, entry_drivers, status, sold_at, sold_price
            FROM positions WHERE id = ?
            """,
            [position_id],
        ).fetchone()
        return self._position_row(row) if row is not None else None

    def close_position(
        self, position_id: str, sold_at: dt.datetime, sold_price: float
    ) -> None:
        """Mark a position sold, recording the date and price."""
        self._con.execute(
            "UPDATE positions SET status = 'sold', sold_at = ?, sold_price = ? WHERE id = ?",
            [sold_at, sold_price, position_id],
        )

    def record_run(
        self,
        job: str,
        status: str,
        detail: str,
        started_at: dt.datetime,
        finished_at: dt.datetime,
    ) -> None:
        """Record a job run for the heartbeat / 'last successful run' check."""
        self._con.execute(
            "INSERT INTO runs (job, status, detail, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [job, status, detail, started_at, finished_at],
        )

    def read_last_run(self, job: str, status: str | None = None) -> dict | None:
        """Return the most recent run for ``job`` (optionally filtered by status)."""
        query = (
            "SELECT job, status, detail, started_at, finished_at FROM runs WHERE job = ?"
        )
        params: list[object] = [job]
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY finished_at DESC LIMIT 1"
        row = self._con.execute(query, params).fetchone()
        if row is None:
            return None
        return {
            "job": row[0],
            "status": row[1],
            "detail": row[2],
            "started_at": row[3].isoformat() if row[3] is not None else None,
            "finished_at": row[4].isoformat() if row[4] is not None else None,
        }

    def save_opportunities(self, scan_ts: dt.datetime, rows: list[dict]) -> int:
        """Replace the stored ranking with a fresh scan (latest scan wins)."""
        self._con.execute("DELETE FROM opportunities")
        for row in rows:
            self._con.execute(
                """
                INSERT INTO opportunities
                    (scan_ts, rank, ticker, conviction, capped_conviction,
                     recommendation, as_of, risk_flags, model_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    scan_ts,
                    row.get("rank"),
                    row.get("ticker"),
                    row.get("conviction"),
                    row.get("capped_conviction"),
                    row.get("recommendation"),
                    row.get("as_of"),
                    json.dumps(row.get("risk_flags", [])),
                    row.get("model_version"),
                ],
            )
        return len(rows)

    def read_latest_opportunities(self, limit: int = 20) -> list[dict]:
        """Return the most recent ranking (top ``limit`` by rank)."""
        rows = self._con.execute(
            """
            SELECT scan_ts, rank, ticker, conviction, capped_conviction,
                   recommendation, as_of, risk_flags, model_version
            FROM opportunities
            ORDER BY rank
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        return [
            {
                "scan_ts": r[0].isoformat() if r[0] is not None else None,
                "rank": r[1],
                "ticker": r[2],
                "conviction": r[3],
                "capped_conviction": r[4],
                "recommendation": r[5],
                "as_of": r[6].isoformat() if r[6] is not None else None,
                "risk_flags": json.loads(r[7]) if r[7] else [],
                "model_version": r[8],
            }
            for r in rows
        ]

    def read_features(self) -> pd.DataFrame:
        """Return all stored feature rows as a DataFrame."""
        return self._con.execute("SELECT * FROM features ORDER BY ticker, as_of").df()
