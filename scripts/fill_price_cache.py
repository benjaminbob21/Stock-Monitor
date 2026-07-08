"""One-time (resumable) fill of the persistent price cache from Tiingo.

Populates ``data/prices.duckdb`` with full history for the benchmark + universe so
that every future retrain reads prices purely from the cache (zero upstream calls).

Throttled to respect Tiingo's free-tier ~50-requests/hour cap: at the default ~80s
spacing, no rolling hour exceeds ~45 calls, so it never 429s. It is **resumable** —
already-cached names only fetch the small recent tail, so if it's interrupted (or a
transient error skips a name), just run it again.

    python scripts/fill_price_cache.py                 # benchmark + full universe
    python scripts/fill_price_cache.py --throttle 5    # faster, only in a clean hour
    python scripts/fill_price_cache.py -t AAPL MSFT     # specific names
"""

from __future__ import annotations

import argparse
import logging

from stock_monitor.config import get_settings
from stock_monitor.pipeline import BENCHMARK
from stock_monitor.providers import get_price_provider
from stock_monitor.providers.price_cache import PriceCache, refresh_price_cache
from stock_monitor.universe import get_universe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill the Tiingo price cache (resumable).")
    parser.add_argument(
        "-t", "--tickers", nargs="+", default=None,
        help="Symbols to fill (default: benchmark + full universe).",
    )
    parser.add_argument(
        "--history-years", type=int, default=None,
        help="Years of history to fetch (default: settings.training_history_years).",
    )
    parser.add_argument(
        "--throttle", type=float, default=80.0,
        help="Seconds between requests to stay under the hourly cap (default 80).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    history_years = args.history_years or settings.training_history_years
    tickers = args.tickers or sorted({BENCHMARK, *get_universe()})

    upstream = get_price_provider(settings)
    cache = PriceCache(settings.price_cache_path)
    print(f"filling price cache: provider={upstream.name} names={len(tickers)} "
          f"history={history_years}y throttle={args.throttle}s -> {settings.price_cache_path}")

    added = refresh_price_cache(
        upstream, cache, tickers, history_years=history_years, throttle_seconds=args.throttle
    )

    total = sum(added.values())
    filled = sum(1 for v in added.values() if v > 0)
    print(f"done: {filled}/{len(tickers)} names touched, {total} rows added")
    print(f"cache now holds {len(cache.cached_tickers())} distinct tickers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
