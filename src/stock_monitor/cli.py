"""Phase 0 CLI: watchlist -> PIT features -> LightGBM score -> explained print.

This is the engine-over-infra proof: pull a hardcoded watchlist, build point-in-time
feature rows, train a small LightGBM on PIT-correct labels (beat SPY over the next
12 months), then print each ticker's conviction score with its top-3 SHAP drivers.

Guardrail: this only *recommends and explains*. You execute every trade. No auto-trading.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import pandas as pd

from stock_monitor.config import get_settings
from stock_monitor.features.builder import build_feature_row, build_training_frame
from stock_monitor.models.scorer import ScoreResult, score_row, train_model
from stock_monitor.providers.edgar_provider import EdgarProvider
from stock_monitor.providers.yfinance_provider import YFinanceProvider

DEFAULT_WATCHLIST: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "JPM", "XOM", "KO")
BENCHMARK = "SPY"
HISTORY_YEARS = 8


def _load_prices(
    provider: YFinanceProvider, ticker: str, start: dt.date, end: dt.date
) -> pd.DataFrame:
    try:
        return provider.get_prices(ticker, start, end)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully on any data-source failure
        print(f"  ! price fetch failed for {ticker}: {exc}", file=sys.stderr)
        return pd.DataFrame()


def _load_fundamentals(provider: EdgarProvider, ticker: str) -> list:
    try:
        return provider.get_fundamentals(ticker)
    except Exception as exc:  # noqa: BLE001 — fundamentals are optional in Phase 0
        print(f"  ! fundamentals fetch failed for {ticker}: {exc}", file=sys.stderr)
        return []


def run(watchlist: list[str]) -> int:
    settings = get_settings()
    prices_provider = YFinanceProvider()
    fundamentals_provider = EdgarProvider()

    end = dt.date.today()
    start = end - dt.timedelta(days=365 * HISTORY_YEARS)

    print(f"Loading benchmark {BENCHMARK} ...")
    benchmark_prices = _load_prices(prices_provider, BENCHMARK, start, end)
    if benchmark_prices.empty:
        print("Cannot proceed: benchmark price history unavailable.", file=sys.stderr)
        return 1

    per_ticker: dict[str, tuple[pd.DataFrame, list]] = {}
    training_frames: list[pd.DataFrame] = []

    for ticker in watchlist:
        print(f"Loading {ticker} ...")
        prices = _load_prices(prices_provider, ticker, start, end)
        if prices.empty:
            continue
        facts = _load_fundamentals(fundamentals_provider, ticker)
        per_ticker[ticker] = (prices, facts)
        frame = build_training_frame(
            ticker, prices, facts, benchmark_prices, settings.label_window_months
        )
        if not frame.empty:
            training_frames.append(frame)

    if not training_frames:
        print("No labelled training data could be assembled.", file=sys.stderr)
        return 1

    pooled = pd.concat(training_frames, ignore_index=True)
    print(f"\nTraining LightGBM on {len(pooled)} PIT rows "
          f"(label = beat {BENCHMARK} over {settings.label_window_months} months) ...")
    try:
        model = train_model(pooled)
    except ValueError as exc:
        print(f"Training aborted: {exc}", file=sys.stderr)
        return 1

    results: list[ScoreResult] = []
    for ticker, (prices, facts) in per_ticker.items():
        as_of = prices.index[-1].date()
        row = build_feature_row(ticker, prices, facts, as_of)
        if row is None:
            continue
        results.append(score_row(model, row))

    results.sort(key=lambda r: r.conviction, reverse=True)
    _print_results(results, settings.label_window_months)
    return 0


def _print_results(results: list[ScoreResult], label_window: int) -> None:
    print("\n" + "=" * 78)
    print(f"CONVICTION (uncalibrated, Phase 0) — horizon: {label_window} months vs {BENCHMARK}")
    print("=" * 78)
    if not results:
        print("No tickers could be scored.")
        return

    for r in results:
        known = r.fundamentals_known_on or "n/a (no PIT fundamentals used)"
        print(f"\n{r.ticker:<6} score {r.conviction:>3}/100   -> {r.recommendation}")
        print(f"       fundamentals known-on: {known}")
        print("       top drivers:")
        for d in r.drivers:
            print(
                f"         {d.direction} {d.feature:<13} "
                f"value={d.value:+.4f}  shap={d.shap:+.4f}"
            )

    print("\n" + "-" * 78)
    print("Decision-support only. You execute every trade — no auto-trading. Score is a")
    print("ranking signal, NOT a calibrated probability (calibration arrives in Phase 2).")
    print("-" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stock-Monitor Phase 0 CLI")
    parser.add_argument(
        "-w",
        "--watchlist",
        nargs="+",
        default=list(DEFAULT_WATCHLIST),
        help="Tickers to score (default: a small sample watchlist).",
    )
    args = parser.parse_args(argv)
    return run([t.upper() for t in args.watchlist])


if __name__ == "__main__":
    raise SystemExit(main())
