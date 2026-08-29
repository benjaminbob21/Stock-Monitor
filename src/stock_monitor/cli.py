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
    parser = argparse.ArgumentParser(description="Stock-Monitor CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Default score command options (or when no subcommand is passed)
    score_parser = subparsers.add_parser("score", help="Score a watchlist with LightGBM")
    score_parser.add_argument(
        "-w",
        "--watchlist",
        nargs="+",
        default=list(DEFAULT_WATCHLIST),
        help="Tickers to score (default: a sample watchlist).",
    )

    # Skew subcommands
    skew_parser = subparsers.add_parser("skew", help="Options Skew Map commands")
    skew_sub = skew_parser.add_subparsers(dest="skew_command")

    run_skew = skew_sub.add_parser("run", help="Run the Options Skew Map scan")
    run_skew.add_argument("--date", help="Snapshot date (YYYY-MM-DD, default: today)")
    run_skew.add_argument(
        "--universe", choices=["core", "sp500"], default="core", help="Universe tier"
    )
    run_skew.add_argument("--force", action="store_true", help="Force re-run if snapshot exists")
    run_skew.add_argument("--workers", type=int, default=6, help="Concurrent workers")

    rep_skew = skew_sub.add_parser("report", help="Print Options Skew Map report")
    rep_skew.add_argument("--date", help="Snapshot date (YYYY-MM-DD, default: latest)")

    exp_skew = skew_sub.add_parser("export", help="Export Options Skew Map to CSV")
    exp_skew.add_argument("--date", help="Snapshot date (YYYY-MM-DD, default: latest)")
    exp_skew.add_argument("--output", help="Custom CSV output path")

    # Direct flag support for legacy `-w`
    parser.add_argument(
        "-w",
        "--watchlist",
        nargs="+",
        help="Tickers to score (legacy shorthand).",
    )

    args = parser.parse_args(argv)

    if args.command == "skew":
        from stock_monitor.skew_report import export_skew_to_csv, format_console_report
        from stock_monitor.skew_service import SkewService
        from stock_monitor.storage.db import Storage

        settings = get_settings()
        storage = Storage(settings.db_path)
        service = SkewService(storage, settings)

        if args.skew_command == "run" or args.skew_command is None:
            snap_date = (
                dt.date.fromisoformat(args.date)
                if getattr(args, "date", None)
                else dt.date.today()
            )
            tier = getattr(args, "universe", "core")
            force = getattr(args, "force", False)
            workers = getattr(args, "workers", 6)
            records, sectors, csv_path = service.run(
                snapshot_date=snap_date,
                tier=tier,
                force=force,
                max_workers=workers,
            )
            print(format_console_report(snap_date, records, sectors))
            if csv_path:
                print(f"\nSaved CSV report to: {csv_path}")
            return 0

        elif args.skew_command == "report":
            target_date = (
                dt.date.fromisoformat(args.date)
                if args.date
                else service.store.get_latest_date()
            )
            if target_date is None:
                print(
                    "No skew snapshots found. Run `stock-monitor skew run` first.",
                    file=sys.stderr,
                )
                return 1
            records, sectors, _ = service.run(snapshot_date=target_date, force=False)
            print(format_console_report(target_date, records, sectors))
            return 0

        elif args.skew_command == "export":
            target_date = (
                dt.date.fromisoformat(args.date)
                if args.date
                else service.store.get_latest_date()
            )
            if target_date is None:
                print(
                    "No skew snapshots found. Run `stock-monitor skew run` first.",
                    file=sys.stderr,
                )
                return 1
            records, _, _ = service.run(snapshot_date=target_date, force=False)
            out_path = args.output or f"data/skew_snapshots/skew_{target_date.isoformat()}.csv"
            saved = export_skew_to_csv(records, out_path)
            print(f"Exported {len(records)} records to {saved}")
            return 0

    # Default score action
    wl_arg = getattr(args, "watchlist", None)
    watchlist = wl_arg or list(DEFAULT_WATCHLIST)
    return run([t.upper() for t in watchlist])


if __name__ == "__main__":
    raise SystemExit(main())
