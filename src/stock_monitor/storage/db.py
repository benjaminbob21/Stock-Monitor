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
import logging
from pathlib import Path
from types import TracebackType

import duckdb
import pandas as pd

from stock_monitor.features.builder import FEATURE_COLUMNS

logger = logging.getLogger(__name__)

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

CREATE TABLE IF NOT EXISTS baskets (
    id VARCHAR PRIMARY KEY,
    name VARCHAR,
    created_at TIMESTAMP DEFAULT now(),
    total_budget DOUBLE,
    status VARCHAR DEFAULT 'open',
    closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS basket_items (
    id VARCHAR PRIMARY KEY,
    basket_id VARCHAR,
    ticker VARCHAR,
    pct DOUBLE,
    budget DOUBLE,
    entry_price DOUBLE,
    shares DOUBLE,
    entry_conviction INTEGER,
    status VARCHAR DEFAULT 'open',
    sold_at TIMESTAMP,
    sold_price DOUBLE
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

CREATE TABLE IF NOT EXISTS alerts (
    ticker VARCHAR,
    kind VARCHAR,
    detail VARCHAR,
    sent_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_picks (
    id VARCHAR PRIMARY KEY,
    ticker VARCHAR,
    pick_date DATE,
    conviction INTEGER,
    recommendation VARCHAR,
    horizon_months INTEGER,
    entry_price DOUBLE,
    benchmark_entry DOUBLE,
    model_version VARCHAR,
    status VARCHAR DEFAULT 'open',
    matured_on DATE,
    exit_price DOUBLE,
    benchmark_exit DOUBLE,
    stock_return DOUBLE,
    benchmark_return DOUBLE,
    excess_return DOUBLE,
    beat_benchmark BOOLEAN,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS news_sentiment (
    ticker VARCHAR NOT NULL,
    date DATE NOT NULL,
    sentiment DOUBLE,
    article_count INTEGER,
    backend VARCHAR,
    ingested_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS news_articles (
    ticker VARCHAR NOT NULL,
    published TIMESTAMP NOT NULL,
    headline VARCHAR NOT NULL,
    source VARCHAR,
    url VARCHAR,
    sentiment DOUBLE,
    backend VARCHAR,
    ingested_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (ticker, published, headline)
);

-- Alternative-sentiment layer (Reddit + financial-media RSS; LLM-reader design,
-- approved 2026-08-28). ``alt_posts`` is the raw audit archive; ``alt_sentiment``
-- holds the LLM's per-ticker verdicts. The legacy FinBERT-era shapes (different
-- PK / missing columns) are detected and migrated in Python — see
-- _migrate_alt_tables; never DROP these here, the verdicts are precious.
CREATE TABLE IF NOT EXISTS alt_posts (
    published TIMESTAMP,
    headline VARCHAR NOT NULL,
    source VARCHAR,
    url VARCHAR,
    engagement INTEGER DEFAULT 0,
    ingested_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (url, headline)
);

CREATE TABLE IF NOT EXISTS alt_sentiment (
    ticker VARCHAR NOT NULL,
    date DATE NOT NULL,
    sentiment DOUBLE,
    buzz INTEGER,
    summary VARCHAR,
    backend VARCHAR,
    ingested_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS news_backfill_state (
    provider VARCHAR NOT NULL,
    ticker VARCHAR NOT NULL,
    covered_through DATE,
    done BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (provider, ticker)
);

CREATE TABLE IF NOT EXISTS macro_series (
    series_id VARCHAR NOT NULL,
    obs_date DATE NOT NULL,
    realtime_start DATE NOT NULL,
    value DOUBLE,
    ingested_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (series_id, obs_date, realtime_start)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    created_at TIMESTAMP DEFAULT now(),
    n_periods INTEGER,
    universe_size INTEGER,
    top_k INTEGER,
    cost_bps DOUBLE,
    strategy_total_return DOUBLE,
    benchmark_total_return DOUBLE,
    excess_return DOUBLE,
    strategy_cagr DOUBLE,
    benchmark_cagr DOUBLE,
    max_drawdown DOUBLE,
    hit_rate DOUBLE
);

CREATE TABLE IF NOT EXISTS skew_daily (
    snapshot_date DATE NOT NULL,
    ticker VARCHAR NOT NULL,
    sector VARCHAR NOT NULL,
    spot DOUBLE,
    ret_1m DOUBLE,
    rel_ret_spy DOUBLE,
    rvol DOUBLE,
    expiration VARCHAR,
    dte_days INTEGER,
    atm_iv DOUBLE,
    call_25d_iv DOUBLE,
    put_25d_iv DOUBLE,
    raw_skew DOUBLE,
    normalized_skew DOUBLE,
    quadrant VARCHAR,
    earnings_date VARCHAR,
    is_earnings_near BOOLEAN,
    sanity_passed BOOLEAN,
    sanity_warning VARCHAR,
    sector_avg_raw_skew DOUBLE,
    sector_avg_norm_skew DOUBLE,
    sector_agreement DOUBLE,
    verdict VARCHAR,
    ingested_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (snapshot_date, ticker)
);

CREATE TABLE IF NOT EXISTS skew_sector_daily (
    snapshot_date DATE NOT NULL,
    sector VARCHAR NOT NULL,
    ticker_count INTEGER,
    avg_raw_skew DOUBLE,
    avg_norm_skew DOUBLE,
    avg_ret_1m DOUBLE,
    agreement DOUBLE,
    dominant_lean VARCHAR,
    ingested_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (snapshot_date, sector)
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
        self._migrate_alt_tables()

    def _migrate_feature_columns(self) -> None:
        """Forward-migrate the features table as FEATURE_COLUMNS grows across phases.

        ``CREATE TABLE IF NOT EXISTS`` won't add new columns to a pre-existing table,
        so an older DB would break inserts. Add any missing feature columns in place
        (no data loss) — the features table is append/upsert-only.
        """
        for column in FEATURE_COLUMNS:
            self._con.execute(f"ALTER TABLE features ADD COLUMN IF NOT EXISTS {column} DOUBLE")

    def _migrate_alt_tables(self) -> None:
        """Migrate the legacy FinBERT-era alt_* shapes to the LLM-reader schema.

        The legacy ``alt_posts`` PK included ``ticker`` and ``alt_sentiment`` had a
        ``post_count`` column; inserts against the new shapes fail with a binder
        error on such DBs. Migrate in place only when the old shape is detected —
        the tables must NOT be dropped unconditionally on every open, or each
        service restart would wipe freshly collected batches.
        """
        cols = {
            t: {
                r[0]
                for r in self._con.execute(
                    "SELECT column_name FROM duckdb_columns() WHERE table_name = ?",
                    [t],
                ).fetchall()
            }
            for t in ("alt_posts", "alt_sentiment")
        }
        if not all(cols.values()):  # tables not created yet — nothing to migrate
            return
        posts_pk: set[str] = set()
        for (names,) in self._con.execute(
            "SELECT constraint_column_names FROM duckdb_constraints() "
            "WHERE table_name = 'alt_posts' AND constraint_type = 'PRIMARY KEY'"
        ).fetchall():
            posts_pk.update(names)
        if posts_pk and "ticker" in posts_pk:
            logger.warning("migrating legacy alt_posts schema (ticker-keyed PK)")
            # DuckDB cannot drop PK constraints, so rebuild the table and copy rows.
            self._con.execute(
                """
                CREATE TABLE alt_posts_migrated (
                    published TIMESTAMP,
                    headline VARCHAR NOT NULL,
                    source VARCHAR,
                    url VARCHAR,
                    engagement INTEGER DEFAULT 0,
                    ingested_at TIMESTAMP DEFAULT now(),
                    PRIMARY KEY (url, headline)
                )
                """
            )
            self._con.execute(
                """
                INSERT OR IGNORE INTO alt_posts_migrated (published, headline, source, url,
                                                           engagement, ingested_at)
                SELECT published, headline, source, url, engagement, ingested_at
                FROM alt_posts
                """
            )
            self._con.execute("DROP TABLE alt_posts")
            self._con.execute("ALTER TABLE alt_posts_migrated RENAME TO alt_posts")
        if "post_count" in cols["alt_sentiment"]:
            logger.warning("migrating legacy alt_sentiment schema (post_count column)")
            self._con.execute("ALTER TABLE alt_sentiment DROP COLUMN post_count;")
            self._con.execute("ALTER TABLE alt_sentiment ADD COLUMN buzz INTEGER;")
            self._con.execute("ALTER TABLE alt_sentiment ADD COLUMN summary VARCHAR;")

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
        # Columns are internal constants (FEATURE_COLUMNS grows across phases), so build
        # the insert list dynamically — new features (e.g. macro) flow through without a
        # hand-edited statement, matching the forward-migration in the constructor.
        cols = ", ".join(_FEATURE_INSERT_COLUMNS)
        try:
            self._con.execute(
                f"""
                INSERT OR REPLACE INTO features ({cols}, ingested_at)
                SELECT {cols}, now()
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
        if table not in {
            "features",
            "scores",
            "quarantine",
            "opportunities",
            "runs",
            "positions",
            "alerts",
            "paper_picks",
            "news_sentiment",
            "news_articles",
            "alt_posts",
            "alt_sentiment",
            "backtest_results",
            "macro_series",
        }:
            raise ValueError(f"unknown table: {table}")
        result = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()
        return int(result[0]) if result else 0

    def record_alert(self, ticker: str, kind: str, detail: str) -> None:
        """Log that an alert was sent (for debouncing)."""
        self._con.execute(
            "INSERT INTO alerts (ticker, kind, detail, sent_at) VALUES (?, ?, ?, now())",
            [ticker, kind, detail],
        )

    def recent_alert(self, ticker: str, kind: str, within_hours: int) -> bool:
        """Return True if an alert of this kind for this ticker was sent recently."""
        row = self._con.execute(
            "SELECT count(*) FROM alerts WHERE ticker = ? AND kind = ? "
            "AND sent_at >= now() - (? * INTERVAL '1 hour')",
            [ticker, kind, within_hours],
        ).fetchone()
        return bool(row and row[0] > 0)

    def last_alert_detail(self, ticker: str, kind: str) -> str | None:
        """Return the ``detail`` of the most recent alert of this kind, or None.

        Used for state-change detection (e.g. only ping when a holding's exit
        signal actually *changes* to 'consider selling', not every hour it sits there).
        """
        row = self._con.execute(
            "SELECT detail FROM alerts WHERE ticker = ? AND kind = ? ORDER BY sent_at DESC LIMIT 1",
            [ticker, kind],
        ).fetchone()
        return row[0] if row else None

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

    def close_position(self, position_id: str, sold_at: dt.datetime, sold_price: float) -> None:
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
        query = "SELECT job, status, detail, started_at, finished_at FROM runs WHERE job = ?"
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

    def read_recent_scores(self, within_days: int = 3) -> list[dict]:
        """Return the newest score per ticker from the ``scores`` table.

        Only scores emitted within ``within_days`` are included, so stale one-off
        lookups don't haunt the ranked page forever. This lets tickers scored
        on-demand (via search or the detail card — names outside the scan universe)
        surface in discovery alongside the nightly ranking.
        """
        rows = self._con.execute(
            """
            SELECT s.ticker, s.as_of, s.conviction, s.recommendation,
                   s.risk_flags, s.model_version, s.drivers
            FROM scores s
            WHERE s.as_of >= current_date - ? * INTERVAL '1' DAY
              AND s.scored_at = (
                    SELECT max(s2.scored_at) FROM scores s2
                    WHERE s2.ticker = s.ticker
              )
            """,
            [within_days],
        ).fetchall()
        return [
            {
                "ticker": r[0],
                "as_of": r[1].isoformat() if r[1] is not None else None,
                "conviction": r[2],
                "recommendation": r[3],
                "risk_flags": json.loads(r[4]) if r[4] else [],
                "model_version": r[5],
                "drivers": json.loads(r[6]) if r[6] else [],
            }
            for r in rows
        ]

    def read_features(self) -> pd.DataFrame:
        """Return all stored feature rows as a DataFrame."""
        return self._con.execute("SELECT * FROM features ORDER BY ticker, as_of").df()

    # ------------------------------------------------------------ news sentiment
    def upsert_news_sentiment(self, df: pd.DataFrame) -> int:
        """Upsert daily per-ticker news sentiment (PIT feature source). Returns rows written.

        Expected columns: ``ticker``, ``date``, ``sentiment``, ``article_count``,
        ``backend``. Idempotent on (ticker, date) so a backfill can be re-run safely.
        """
        if df is None or df.empty:
            return 0
        cols = ["ticker", "date", "sentiment", "article_count", "backend"]
        frame = df[cols].copy()
        self._con.register("_news_tmp", frame)
        try:
            self._con.execute(
                """
                INSERT INTO news_sentiment (ticker, date, sentiment, article_count, backend)
                SELECT ticker, date, sentiment, article_count, backend FROM _news_tmp
                ON CONFLICT (ticker, date) DO UPDATE SET
                    sentiment = excluded.sentiment,
                    article_count = excluded.article_count,
                    backend = excluded.backend
                """
            )
        finally:
            self._con.unregister("_news_tmp")
        return int(len(frame))

    def read_news_sentiment(self, ticker: str | None = None) -> pd.DataFrame:
        """Return stored daily news sentiment, optionally for a single ticker."""
        if ticker:
            return self._con.execute(
                "SELECT * FROM news_sentiment WHERE ticker = ? ORDER BY date",
                [ticker.upper()],
            ).df()
        return self._con.execute("SELECT * FROM news_sentiment ORDER BY ticker, date").df()

    def latest_news_date(self) -> dt.date | None:
        """Return the most recent day we have stored news sentiment for.

        Freshness signal for the UI: how many days behind is our news? Returns ``None``
        when no news has ever been stored.
        """
        row = self._con.execute("SELECT MAX(date) FROM news_sentiment").fetchone()
        return row[0] if row and row[0] is not None else None

    # ----------------------------------------------------------------- macro series
    def upsert_macro_series(self, df: pd.DataFrame) -> int:
        """Insert-or-replace macro vintages keyed by (series_id, obs_date, realtime_start)."""
        if df is None or df.empty:
            return 0
        sub = df.reindex(columns=["series_id", "obs_date", "realtime_start", "value"]).copy()
        self._con.register("_incoming_macro", sub)
        try:
            self._con.execute(
                """
                INSERT OR REPLACE INTO macro_series
                    (series_id, obs_date, realtime_start, value, ingested_at)
                SELECT series_id, obs_date, realtime_start, value, now()
                FROM _incoming_macro
                """
            )
        finally:
            self._con.unregister("_incoming_macro")
        return len(sub)

    def read_macro_series(self, series_id: str | None = None) -> pd.DataFrame:
        """Return stored macro vintages, optionally for a single series."""
        if series_id:
            return self._con.execute(
                "SELECT series_id, obs_date, realtime_start, value FROM macro_series "
                "WHERE series_id = ? ORDER BY obs_date, realtime_start",
                [series_id],
            ).df()
        return self._con.execute(
            "SELECT series_id, obs_date, realtime_start, value FROM macro_series "
            "ORDER BY series_id, obs_date, realtime_start"
        ).df()

    # ------------------------------------------------------------ news backfill state
    def get_backfill_state(self, provider: str) -> dict[str, tuple[dt.date | None, bool]]:
        """Return per-ticker backfill progress for ``provider`` as ``{ticker: (through, done)}``.

        Lets the throttled, resumable gap backfill skip finished names and resume the
        rest from where the last run stopped (so the 25/day quota is never wasted).
        """
        rows = self._con.execute(
            "SELECT ticker, covered_through, done FROM news_backfill_state WHERE provider = ?",
            [provider],
        ).fetchall()
        return {r[0]: (r[1], bool(r[2])) for r in rows}

    def upsert_backfill_state(
        self, provider: str, ticker: str, covered_through: dt.date | None, done: bool
    ) -> None:
        """Record how far a ticker's gap backfill has progressed (idempotent per key)."""
        self._con.execute(
            """
            INSERT INTO news_backfill_state (provider, ticker, covered_through, done, updated_at)
            VALUES (?, ?, ?, ?, now())
            ON CONFLICT (provider, ticker) DO UPDATE SET
                covered_through = excluded.covered_through,
                done = excluded.done,
                updated_at = now()
            """,
            [provider, ticker.upper(), covered_through, done],
        )

    def upsert_news_articles(self, df: pd.DataFrame) -> int:
        """Upsert raw news headlines (permanent archive). Returns rows written.

        Expected columns: ``ticker``, ``published`` (datetime), ``headline``,
        ``source``, ``url``, ``sentiment``, ``backend``. We keep the headline + link
        (not the article body) so the news can be re-scored later without re-buying it.
        Idempotent on (ticker, published, headline); undated rows are dropped since the
        publish timestamp anchors the permanent key.
        """
        if df is None or df.empty:
            return 0
        cols = ["ticker", "published", "headline", "source", "url", "sentiment", "backend"]
        frame = df[cols].copy()
        frame = frame.dropna(subset=["published", "headline"])
        if frame.empty:
            return 0
        self._con.register("_articles_tmp", frame)
        try:
            self._con.execute(
                """
                INSERT INTO news_articles
                    (ticker, published, headline, source, url, sentiment, backend)
                SELECT ticker, published, headline, source, url, sentiment, backend
                FROM _articles_tmp
                ON CONFLICT (ticker, published, headline) DO UPDATE SET
                    source = excluded.source,
                    url = excluded.url,
                    sentiment = excluded.sentiment,
                    backend = excluded.backend
                """
            )
        finally:
            self._con.unregister("_articles_tmp")
        return int(len(frame))

    def record_alt_posts(self, posts: list[dict]) -> int:
        """Archive raw alt-sentiment posts (audit trail; ticker assigned by the LLM later).

        Expected keys: ``published`` (nullable), ``text``/``headline``, ``source``,
        ``url``, ``engagement``. Idempotent on (url, headline); rows without a URL
        are dropped since the URL anchors the permanent key.
        """
        if not posts:
            return 0
        frame = pd.DataFrame(
            [
                {
                    "published": p.get("published"),
                    "headline": p.get("headline") or p.get("text") or "",
                    "source": p.get("source"),
                    "url": p.get("url"),
                    "engagement": int(p.get("engagement") or 0),
                }
                for p in posts
            ]
        ).dropna(subset=["url", "headline"])
        frame = frame[frame["headline"] != ""]
        if frame.empty:
            return 0
        self._con.register("_alt_tmp", frame)
        try:
            self._con.execute(
                """
                INSERT INTO alt_posts (published, headline, source, url, engagement)
                SELECT published, headline, source, url, engagement FROM _alt_tmp
                ON CONFLICT (url, headline) DO NOTHING
                """
            )
        finally:
            self._con.unregister("_alt_tmp")
        return int(len(frame))

    def upsert_alt_sentiment_llm(self, rows: list[dict]) -> int:
        """Upsert LLM-derived per-ticker alt-sentiment verdicts. Returns rows written.

        Expected keys: ``ticker``, ``date``, ``sentiment``, ``buzz``, ``summary``,
        ``backend``. Idempotent on (ticker, date).
        """
        if not rows:
            return 0
        frame = pd.DataFrame(rows)
        self._con.register("_alt_llm_tmp", frame)
        try:
            self._con.execute(
                """
                INSERT INTO alt_sentiment (ticker, date, sentiment, buzz, summary, backend)
                SELECT ticker, date, sentiment, buzz, summary, backend FROM _alt_llm_tmp
                ON CONFLICT (ticker, date) DO UPDATE SET
                    sentiment = excluded.sentiment,
                    buzz = excluded.buzz,
                    summary = excluded.summary,
                    backend = excluded.backend
                """
            )
        finally:
            self._con.unregister("_alt_llm_tmp")
        return int(len(frame))

    def read_alt_sentiment(self, ticker: str | None = None) -> pd.DataFrame:
        """Return stored daily alt-sentiment, optionally for a single ticker."""
        if ticker:
            return self._con.execute(
                "SELECT * FROM alt_sentiment WHERE ticker = ? ORDER BY date",
                [ticker.upper()],
            ).df()
        return self._con.execute("SELECT * FROM alt_sentiment ORDER BY ticker, date").df()

    def read_news_articles(self, ticker: str | None = None) -> pd.DataFrame:
        """Return stored raw headlines, optionally for a single ticker (newest first)."""
        if ticker:
            return self._con.execute(
                "SELECT * FROM news_articles WHERE ticker = ? ORDER BY published DESC",
                [ticker.upper()],
            ).df()
        return self._con.execute("SELECT * FROM news_articles ORDER BY ticker, published DESC").df()

    # ------------------------------------------------------------------ paper mode
    def create_basket(
        self,
        basket_id: str,
        name: str,
        created_at: dt.datetime,
        total_budget: float,
        items: list[dict],
    ) -> None:
        """Record a joint portfolio (basket): total budget split across tickers."""
        self._con.execute(
            """
            INSERT INTO baskets (id, name, created_at, total_budget, status)
            VALUES (?, ?, ?, ?, 'open')
            """,
            [basket_id, name, created_at, total_budget],
        )
        for item in items:
            self._con.execute(
                """
                INSERT INTO basket_items
                    (id, basket_id, ticker, pct, budget, entry_price, shares,
                     entry_conviction, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
                """,
                [
                    item["id"],
                    basket_id,
                    item["ticker"],
                    item["pct"],
                    item["budget"],
                    item["entry_price"],
                    item["shares"],
                    item.get("entry_conviction"),
                ],
            )

    def _basket_row(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "name": row[1],
            "created_at": row[2].isoformat() if row[2] is not None else None,
            "total_budget": row[3],
            "status": row[4],
            "closed_at": row[5].isoformat() if row[5] is not None else None,
        }

    def _basket_item_row(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "basket_id": row[1],
            "ticker": row[2],
            "pct": row[3],
            "budget": row[4],
            "entry_price": row[5],
            "shares": row[6],
            "entry_conviction": row[7],
            "status": row[8],
            "sold_at": row[9].isoformat() if row[9] is not None else None,
            "sold_price": row[10],
        }

    def list_baskets(self, status: str | None = None) -> list[dict]:
        """Return baskets, newest first, optionally filtered by status."""
        if status:
            rows = self._con.execute(
                """
                SELECT id, name, created_at, total_budget, status, closed_at
                FROM baskets WHERE status = ? ORDER BY created_at DESC
                """,
                [status],
            ).fetchall()
        else:
            rows = self._con.execute(
                """
                SELECT id, name, created_at, total_budget, status, closed_at
                FROM baskets ORDER BY created_at DESC
                """
            ).fetchall()
        return [self._basket_row(r) for r in rows]

    def get_basket(self, basket_id: str) -> dict | None:
        row = self._con.execute(
            """
            SELECT id, name, created_at, total_budget, status, closed_at
            FROM baskets WHERE id = ?
            """,
            [basket_id],
        ).fetchone()
        return self._basket_row(row) if row is not None else None

    def list_basket_items(self, basket_id: str) -> list[dict]:
        """Return the constituent legs of a basket, largest weight first."""
        rows = self._con.execute(
            """
            SELECT id, basket_id, ticker, pct, budget, entry_price, shares,
                   entry_conviction, status, sold_at, sold_price
            FROM basket_items WHERE basket_id = ? ORDER BY pct DESC
            """,
            [basket_id],
        ).fetchall()
        return [self._basket_item_row(r) for r in rows]

    def sell_basket_item(self, item_id: str, sold_at: dt.datetime, sold_price: float) -> None:
        """Mark one leg of a basket sold at a price (the other legs stay open)."""
        self._con.execute(
            "UPDATE basket_items SET status = 'sold', sold_at = ?, sold_price = ? WHERE id = ?",
            [sold_at, sold_price, item_id],
        )

    def close_basket(self, basket_id: str, closed_at: dt.datetime) -> None:
        """Mark a whole basket closed (all open legs go with it)."""
        self._con.execute(
            "UPDATE baskets SET status = 'closed', closed_at = ? WHERE id = ?",
            [closed_at, basket_id],
        )
        self._con.execute(
            "UPDATE basket_items SET status = 'sold', sold_at = ? "
            "WHERE basket_id = ? AND status = 'open'",
            [closed_at, basket_id],
        )

    def record_paper_pick(
        self,
        *,
        pick_id: str,
        ticker: str,
        pick_date: dt.date,
        conviction: int,
        recommendation: str,
        horizon_months: int,
        entry_price: float,
        benchmark_entry: float,
        model_version: str,
        matured_on: dt.date,
    ) -> bool:
        """Record a paper pick idempotently. Returns True if a new row was inserted.

        The pick is a *simulated* buy — the engine's daily conviction call, logged with
        the price it would have paid, so we can later score the recommendation against
        the benchmark with zero real money at risk (build-plan Phase 4: paper mode).
        """
        before = self.count("paper_picks")
        self._con.execute(
            """
            INSERT OR IGNORE INTO paper_picks
                (id, ticker, pick_date, conviction, recommendation, horizon_months,
                 entry_price, benchmark_entry, model_version, status, matured_on)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            [
                pick_id,
                ticker,
                pick_date,
                conviction,
                recommendation,
                horizon_months,
                entry_price,
                benchmark_entry,
                model_version,
                matured_on,
            ],
        )
        return self.count("paper_picks") > before

    def _paper_row(self, row: tuple) -> dict:
        return {
            "id": row[0],
            "ticker": row[1],
            "pick_date": row[2].isoformat() if row[2] is not None else None,
            "conviction": row[3],
            "recommendation": row[4],
            "horizon_months": row[5],
            "entry_price": row[6],
            "benchmark_entry": row[7],
            "model_version": row[8],
            "status": row[9],
            "matured_on": row[10].isoformat() if row[10] is not None else None,
            "exit_price": row[11],
            "benchmark_exit": row[12],
            "stock_return": row[13],
            "benchmark_return": row[14],
            "excess_return": row[15],
            "beat_benchmark": row[16],
        }

    def list_paper_picks(self, status: str | None = None) -> list[dict]:
        """Return paper picks (optionally filtered by 'open'/'closed'), newest first."""
        query = (
            "SELECT id, ticker, pick_date, conviction, recommendation, horizon_months, "
            "entry_price, benchmark_entry, model_version, status, matured_on, exit_price, "
            "benchmark_exit, stock_return, benchmark_return, excess_return, beat_benchmark "
            "FROM paper_picks"
        )
        params: list[object] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY pick_date DESC, ticker"
        rows = self._con.execute(query, params).fetchall()
        return [self._paper_row(r) for r in rows]

    def close_paper_pick(
        self,
        pick_id: str,
        *,
        exit_price: float,
        benchmark_exit: float,
        stock_return: float,
        benchmark_return: float,
        excess_return: float,
        beat_benchmark: bool,
    ) -> None:
        """Mark a matured paper pick closed, recording its realised return vs benchmark."""
        self._con.execute(
            """
            UPDATE paper_picks SET
                status = 'closed', exit_price = ?, benchmark_exit = ?,
                stock_return = ?, benchmark_return = ?, excess_return = ?,
                beat_benchmark = ?
            WHERE id = ?
            """,
            [
                exit_price,
                benchmark_exit,
                stock_return,
                benchmark_return,
                excess_return,
                beat_benchmark,
                pick_id,
            ],
        )

    def save_backtest_result(
        self,
        *,
        n_periods: int,
        universe_size: int,
        top_k: int,
        cost_bps: float,
        strategy_total_return: float,
        benchmark_total_return: float,
        excess_return: float,
        strategy_cagr: float,
        benchmark_cagr: float,
        max_drawdown: float,
        hit_rate: float,
    ) -> None:
        """Persist one walk-forward backtest run (the historical half of the scorecard)."""
        self._con.execute(
            """
            INSERT INTO backtest_results
                (n_periods, universe_size, top_k, cost_bps, strategy_total_return,
                 benchmark_total_return, excess_return, strategy_cagr, benchmark_cagr,
                 max_drawdown, hit_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                n_periods,
                universe_size,
                top_k,
                cost_bps,
                strategy_total_return,
                benchmark_total_return,
                excess_return,
                strategy_cagr,
                benchmark_cagr,
                max_drawdown,
                hit_rate,
            ],
        )

    def latest_backtest(self) -> dict | None:
        """Return the most recent stored backtest result, or ``None`` if none exist."""
        row = self._con.execute(
            """
            SELECT created_at, n_periods, universe_size, top_k, cost_bps,
                   strategy_total_return, benchmark_total_return, excess_return,
                   strategy_cagr, benchmark_cagr, max_drawdown, hit_rate
            FROM backtest_results
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "created_at": row[0].isoformat() if row[0] is not None else None,
            "n_periods": row[1],
            "universe_size": row[2],
            "top_k": row[3],
            "cost_bps": row[4],
            "strategy_total_return": row[5],
            "benchmark_total_return": row[6],
            "excess_return": row[7],
            "strategy_cagr": row[8],
            "benchmark_cagr": row[9],
            "max_drawdown": row[10],
            "hit_rate": row[11],
        }
