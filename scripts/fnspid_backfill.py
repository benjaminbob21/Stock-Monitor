"""One-time FNSPID historical-news backfill into ``news_sentiment`` (+ optional archive).

FNSPID (https://huggingface.co/datasets/Zihan1004/FNSPID) is a free financial-news
dataset covering S&P 500 companies, 1999-2023. This loader turns those past headlines
into the model's trainable ``sentiment`` feature, which otherwise trains as 0.0 because
we never had historical news.

What it does:
  1. Streams the FNSPID news CSV once (via DuckDB) into a slim parquet, filtered to our
     scan universe + a date range (keeps memory + compute bounded).
  2. For each ticker, FinBERT-scores the headlines and aggregates them to one sentiment
     value per calendar day, then upserts that daily series into ``news_sentiment`` (the
     PIT feature source ``run_training`` now reads).
  3. Optionally (``--archive``) also stores the raw headlines in ``news_articles``.

Run it LOCALLY (FinBERT + disk live here), then sync the DuckDB file to the VM.

Step 0 - download the news CSV (~22 GB, one time):
    wget https://huggingface.co/datasets/Zihan1004/FNSPID/resolve/main/Stock_news/nasdaq_exteral_data.csv

Step 1 - backfill (defaults to the S&P 500 universe, from 2010):
    .venv/bin/python scripts/fnspid_backfill.py --csv nasdaq_exteral_data.csv

The job is idempotent and resumable: already-loaded tickers are skipped unless --force.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import time

import duckdb

from stock_monitor.config import get_settings
from stock_monitor.sentiment import get_sentiment_analyzer
from stock_monitor.storage.db import Storage
from stock_monitor.universe import DEFAULT_UNIVERSE, fetch_sp500_symbols

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]+$")


def _resolve_universe(mode: str) -> set[str]:
    """Return the set of tickers to keep (empty set = no ticker filter)."""
    mode = mode.lower()
    if mode == "all":
        return set()
    names = fetch_sp500_symbols() if mode == "sp500" else list(DEFAULT_UNIVERSE)
    return {t.upper() for t in names if _TICKER_RE.match(t.upper())}


def _resolve_columns(header: list[str]) -> dict[str, str | None]:
    """Map FNSPID's CSV headers to our canonical fields, tolerant of naming drift."""
    low = {h.lower(): h for h in header}

    def pick(exacts: list[str], contains: list[str]) -> str | None:
        for e in exacts:
            if e.lower() in low:
                return low[e.lower()]
        for c in contains:
            for h in header:
                if c in h.lower():
                    return h
        return None

    cols = {
        "ticker": pick(["Stock_symbol"], ["symbol", "ticker", "stock"]),
        "date": pick(["Date"], ["date", "time"]),
        "headline": pick(["Article_title"], ["title", "headline"]),
        "url": pick(["Url"], ["url", "link"]),
        "source": pick(["Publisher"], ["publisher", "source", "author"]),
    }
    missing = [k for k in ("ticker", "date", "headline") if not cols[k]]
    if missing:
        raise SystemExit(f"Could not find columns {missing} in CSV header: {header}")
    return cols


def _build_slim(
    csv: str,
    slim: str,
    cols: dict[str, str | None],
    universe: set[str],
    from_date: dt.date,
    to_date: dt.date,
) -> int:
    """Stream the big CSV once into a slim, filtered parquet. Returns row count."""
    tkr, dcol, hcol = cols["ticker"], cols["date"], cols["headline"]
    url_expr = f'"{cols["url"]}"' if cols["url"] else "NULL"
    src_expr = f'"{cols["source"]}"' if cols["source"] else "NULL"

    where = [f'"{hcol}" IS NOT NULL', f'TRY_CAST("{dcol}" AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)']
    if universe:
        inlist = ",".join(f"'{t}'" for t in sorted(universe))
        where.append(f'upper("{tkr}") IN ({inlist})')

    sql = f"""
        COPY (
            SELECT upper("{tkr}") AS ticker,
                   TRY_CAST("{dcol}" AS TIMESTAMP) AS published,
                   "{hcol}" AS headline,
                   {url_expr} AS url,
                   {src_expr} AS source
            FROM read_csv_auto(?, all_varchar=true, ignore_errors=true,
                               quote='"', escape='"')
            WHERE {" AND ".join(where)}
        ) TO '{slim}' (FORMAT PARQUET)
    """
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute(sql, [csv, from_date.isoformat(), to_date.isoformat()])
    n = con.execute(f"SELECT count(*) FROM '{slim}'").fetchone()[0]
    con.close()
    return int(n)


def _write_cache(con: duckdb.DuckDBPyConnection, cache: dict[str, float], path: str) -> None:
    """Persist the headline->score cache to parquet (resume point)."""
    import pandas as pd

    df = pd.DataFrame({"headline": list(cache.keys()), "score": list(cache.values())})
    con.register("cache_df", df)
    con.execute(f"COPY cache_df TO '{path}' (FORMAT PARQUET)")
    con.unregister("cache_df")


def _score_distinct(
    slim: str,
    cache_path: str,
    *,
    checkpoint_every: int,
    batch_size: int,
) -> tuple[dict[str, float], str]:
    """Score every DISTINCT headline once (dedup), checkpointing to a resumable cache.

    Returns (headline->score map, backend name). This is the long, CPU-bound phase; it
    holds NO database lock, so the live backend can keep serving from the main DuckDB.
    """
    import os

    analyzer = get_sentiment_analyzer(get_settings())
    try:  # let FinBERT use every core on this box
        import torch

        torch.set_num_threads(os.cpu_count() or 1)
    except Exception:  # noqa: BLE001 — VADER path or torch missing
        pass

    con = duckdb.connect()
    distinct = [
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT headline FROM '{slim}' WHERE headline IS NOT NULL"
        ).fetchall()
    ]

    cache: dict[str, float] = {}
    if os.path.exists(cache_path):
        for h, s in con.execute(f"SELECT headline, score FROM '{cache_path}'").fetchall():
            cache[h] = float(s)

    todo = [h for h in distinct if h not in cache]
    print(
        f"backend={analyzer.name} distinct={len(distinct):,} cached={len(cache):,} "
        f"to_score={len(todo):,}",
        flush=True,
    )

    since = 0
    t0 = time.time()
    for i in range(0, len(todo), checkpoint_every):
        chunk = todo[i : i + checkpoint_every]
        scores = analyzer.score_batch(chunk, batch_size=batch_size) if analyzer.name == "finbert" \
            else analyzer.score_batch(chunk)
        for h, sc in zip(chunk, scores):
            cache[h] = float(sc)
        since += len(chunk)
        _write_cache(con, cache, cache_path)
        done = i + len(chunk)
        rate = done / max(time.time() - t0, 1e-6)
        eta = (len(todo) - done) / max(rate, 1e-6)
        print(
            f"  scored {done:,}/{len(todo):,} ({rate:.0f}/s, eta {eta/3600:.1f}h)",
            flush=True,
        )
    con.close()
    return cache, analyzer.name


def _process(
    slim: str,
    db_path: str,
    max_per_day: int,
    *,
    archive: bool,
    force: bool,
    cache_path: str = "data/fnspid_scores.parquet",
    checkpoint_every: int = 20000,
    batch_size: int = 64,
) -> tuple[int, int, int]:
    """Two-phase backfill: score distinct headlines (dedup), then aggregate per ticker/day."""
    import pandas as pd

    cache, backend = _score_distinct(
        slim, cache_path, checkpoint_every=checkpoint_every, batch_size=batch_size
    )

    con = duckdb.connect()
    tickers = [r[0] for r in con.execute(f"SELECT DISTINCT ticker FROM '{slim}' ORDER BY ticker").fetchall()]

    scored_tickers = days = archived = 0
    with Storage(db_path) as store:
        for i, tkr in enumerate(tickers, 1):
            if not force and not store.read_news_sentiment(tkr).empty:
                print(f"[{i}/{len(tickers)}] {tkr}: already loaded, skipping", flush=True)
                continue

            rows = con.execute(
                f"SELECT published, headline, url, source FROM '{slim}' "
                "WHERE ticker = ? ORDER BY published",
                [tkr],
            ).fetchall()

            by_day: dict[dt.date, list[tuple[str, str, str, object]]] = {}
            for p, h, u, s in rows:
                if p is None or not h:
                    continue
                day = p.date() if hasattr(p, "date") else p
                bucket = by_day.setdefault(day, [])
                if len(bucket) < max_per_day:
                    bucket.append((h, u or "", s or "", p))
            if not by_day:
                continue

            daily_rows = []
            arch_rows = []
            for day, bucket in by_day.items():
                scores = [cache.get(h, 0.0) for (h, _u, _s, _p) in bucket]
                daily_rows.append(
                    {
                        "ticker": tkr,
                        "date": day,
                        "sentiment": float(sum(scores) / len(scores)),
                        "article_count": len(bucket),
                        "backend": backend,
                    }
                )
                if archive:
                    for (h, u, s, p), sc in zip(bucket, scores):
                        arch_rows.append(
                            {
                                "ticker": tkr,
                                "published": pd.Timestamp(p),
                                "headline": h,
                                "source": s,
                                "url": u,
                                "sentiment": float(sc),
                                "backend": backend,
                            }
                        )

            daily = pd.DataFrame(daily_rows).sort_values("date").reset_index(drop=True)
            days += store.upsert_news_sentiment(daily)
            scored_tickers += 1
            if archive and arch_rows:
                arch = (
                    pd.DataFrame(arch_rows)
                    .drop_duplicates(subset=["ticker", "published", "headline"])
                    .sort_values("published")
                    .reset_index(drop=True)
                )
                archived += store.upsert_news_articles(arch)
            print(f"[{i}/{len(tickers)}] {tkr}: {len(daily)} days upserted", flush=True)
    con.close()
    return scored_tickers, days, archived


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="FNSPID historical-news backfill")
    parser.add_argument("--csv", help="Path to FNSPID nasdaq_exteral_data.csv (needed to build the slim parquet)")
    parser.add_argument("--slim", default="data/fnspid_slim.parquet", help="Intermediate parquet path")
    parser.add_argument("--universe", default="sp500", choices=["sp500", "default", "all"])
    parser.add_argument("--from-date", default="2010-01-01", help="Earliest headline date (YYYY-MM-DD)")
    parser.add_argument("--to-date", default=dt.date.today().isoformat(), help="Latest headline date")
    parser.add_argument("--max-per-day", type=int, default=settings.news_backfill_max_per_day,
                        help="Cap headlines scored per ticker per day (bounds FinBERT cost)")
    parser.add_argument("--cache", default="data/fnspid_scores.parquet",
                        help="Resumable headline->score cache parquet (distinct headlines only)")
    parser.add_argument("--batch-size", type=int, default=64, help="FinBERT inference batch size")
    parser.add_argument("--checkpoint-every", type=int, default=20000,
                        help="Flush the score cache every N headlines")
    parser.add_argument("--archive", action="store_true",
                        help="Also store raw headlines in news_articles (re-scores every headline; slower + more disk)")
    parser.add_argument("--reuse-slim", action="store_true", help="Skip rebuilding the slim parquet if it exists")
    parser.add_argument("--build-only", action="store_true",
                        help="Only build the slim parquet, then exit (run this on the machine with the 22GB CSV)")
    parser.add_argument("--force", action="store_true", help="Re-score tickers already present in news_sentiment")
    args = parser.parse_args(argv)

    import os

    from_date = dt.date.fromisoformat(args.from_date)
    to_date = dt.date.fromisoformat(args.to_date)
    universe = _resolve_universe(args.universe)

    building = not (args.reuse_slim and os.path.exists(args.slim))
    if building:
        if not args.csv:
            raise SystemExit("--csv is required to build the slim parquet (or pass --reuse-slim with an existing --slim)")
        print(f"universe={args.universe} ({len(universe) or 'all'} tickers), "
              f"range {from_date}..{to_date}", flush=True)
        import pandas as pd

        header = list(pd.read_csv(args.csv, nrows=0).columns)
        cols = _resolve_columns(header)
        print(f"detected columns: {cols}", flush=True)
        os.makedirs(os.path.dirname(args.slim) or ".", exist_ok=True)
        t0 = time.time()
        n = _build_slim(args.csv, args.slim, cols, universe, from_date, to_date)
        print(f"slim parquet built: {n:,} rows in {time.time() - t0:.0f}s -> {args.slim}", flush=True)
    else:
        print(f"reusing existing slim parquet: {args.slim}", flush=True)

    if args.build_only:
        print("build-only: slim parquet ready. Transfer it to the FinBERT host, then "
              "re-run there with --reuse-slim.", flush=True)
        return 0

    print(f"scoring with max_per_day={args.max_per_day} (archive={args.archive}) ...", flush=True)
    t0 = time.time()
    scored, days, archived = _process(
        args.slim, settings.db_path, args.max_per_day, archive=args.archive, force=args.force,
        cache_path=args.cache, checkpoint_every=args.checkpoint_every, batch_size=args.batch_size,
    )
    print(
        f"DONE: {scored} tickers scored, {days:,} daily-sentiment rows upserted, "
        f"{archived:,} headlines archived, in {time.time() - t0:.0f}s.",
        flush=True,
    )
    print("Next: retrain (`stock-monitor-train -w <tickers>`), then sync the DuckDB to the VM.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
