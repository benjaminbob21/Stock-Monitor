"""One-off: merge the FNSPID backfill's news_sentiment into the main DuckDB.

The FinBERT backfill wrote to a SEPARATE DuckDB (data/fnspid_news.duckdb) so it
never contended with the live backend's single-writer lock on the main DB. This
script upserts those rows into the main DB. Run it with the live service STOPPED
so this process can hold the main DB's write lock.

    .venv/bin/python scripts/merge_fnspid_sentiment.py \
        --main data/stock_monitor.duckdb --src data/fnspid_news.duckdb
"""

from __future__ import annotations

import argparse
import sys

import duckdb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default="data/stock_monitor.duckdb")
    ap.add_argument("--src", default="data/fnspid_news.duckdb")
    args = ap.parse_args(argv)

    con = duckdb.connect(args.main)  # read-write; needs the lock (service stopped)
    try:
        cols = [c[1] for c in con.execute("PRAGMA table_info(news_sentiment)").fetchall()]
        print("main.news_sentiment cols:", cols)
        before = con.execute("SELECT count(*) FROM news_sentiment").fetchone()[0]
        distinct_before = con.execute(
            "SELECT count(DISTINCT ticker) FROM news_sentiment"
        ).fetchone()[0]
        print(f"before: {before:,} rows, {distinct_before} tickers")

        con.execute(f"ATTACH '{args.src}' AS fn (READ_ONLY)")
        src_rows = con.execute("SELECT count(*) FROM fn.news_sentiment").fetchone()[0]
        print(f"source: {src_rows:,} rows to upsert")

        con.execute(
            """
            INSERT INTO news_sentiment (ticker, date, sentiment, article_count, backend)
            SELECT ticker, date, sentiment, article_count, backend
            FROM fn.news_sentiment
            ON CONFLICT (ticker, date) DO UPDATE SET
                sentiment = excluded.sentiment,
                article_count = excluded.article_count,
                backend = excluded.backend,
                ingested_at = now()
            """
        )

        after = con.execute("SELECT count(*) FROM news_sentiment").fetchone()[0]
        distinct_after = con.execute(
            "SELECT count(DISTINCT ticker) FROM news_sentiment"
        ).fetchone()[0]
        rng = con.execute("SELECT min(date), max(date) FROM news_sentiment").fetchone()
        nonzero = con.execute(
            "SELECT count(*) FROM news_sentiment WHERE sentiment <> 0"
        ).fetchone()[0]
        print(f"after:  {after:,} rows (+{after - before:,} new), {distinct_after} tickers")
        print(f"date range: {rng[0]} .. {rng[1]}")
        print(f"non-zero sentiment rows: {nonzero:,}")
        print("sample:")
        for row in con.execute(
            "SELECT ticker, date, sentiment, article_count, backend "
            "FROM news_sentiment WHERE backend = 'finbert' ORDER BY random() LIMIT 5"
        ).fetchall():
            print("  ", row)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
