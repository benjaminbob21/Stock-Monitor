"""CLI entry points to exercise the short-horizon track end-to-end.

``stock-monitor-short-train``
    Builds PIT-correct short-horizon training rows (market + event features),
    trains the heavily-regularized calibrated model, prints walk-forward
    precision/recall@70 plus the market-only vs market+events ablation, and
    optionally saves through the rollback-safe model registry.

``stock-monitor-short-alerts``
    One paper-mode alert cycle: fetch recent headlines into the events table
    (PIT ``known_at`` semantics), rescore the watchlist with the saved short
    model, and deliver candidate signals through the configured notifier.

Guardrail: these commands *recommend and explain*. Nothing is ever executed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

import pandas as pd

from stock_monitor.alerts.paper import (
    event_records_from_rows,
    run_paper_alerts,
)
from stock_monitor.config import get_settings
from stock_monitor.events import EventRecord
from stock_monitor.features.builder import build_feature_row
from stock_monitor.features.events import build_event_features
from stock_monitor.features.labels import build_short_horizon_training_rows
from stock_monitor.models.registry import load_model, save_model
from stock_monitor.models.short_horizon import (
    ablation_evaluate,
    train_short_calibrated_model,
)
from stock_monitor.notify import get_notifier
from stock_monitor.providers.edgar_provider import EdgarProvider
from stock_monitor.providers.yfinance_provider import YFinanceProvider
from stock_monitor.sentiment import FinnhubNewsProvider, YFinanceNewsProvider
from stock_monitor.storage.db import Storage

logger = logging.getLogger(__name__)

DEFAULT_SHORT_MODEL_PATH = "models/latest_short_event.joblib"
HISTORY_YEARS = 8
GRID_STEP_DAYS = 5  # weekly-ish as-of grid keeps row counts tractable


def _load_prices(provider: YFinanceProvider, ticker: str, start: dt.date, end: dt.date):
    try:
        return provider.get_prices(ticker, start, end)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully per-ticker
        print(f"  ! price fetch failed for {ticker}: {exc}", file=sys.stderr)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def _as_of_grid(prices: pd.DataFrame) -> list[pd.Timestamp]:
    """Weekly-spaced decision dates with >=253 past bars and >=25 future bars."""
    if prices.empty:
        return []
    bars = prices.index
    lo, hi = 260, len(bars) - 26
    if hi <= lo:
        return []
    return [bars[i] for i in range(lo, hi, GRID_STEP_DAYS)]


def train_short(
    tickers: list[str],
    label_column: str,
    save_path: str | None,
) -> int:
    settings = get_settings()
    price_provider = YFinanceProvider()
    fundamental_provider = EdgarProvider()

    end = dt.date.today()
    start = end - dt.timedelta(days=365 * HISTORY_YEARS)

    print(f"Loading benchmark SPY ({start} -> {end}) ...")
    benchmark = _load_prices(price_provider, "SPY", start, end)
    if benchmark.empty:
        print("Cannot proceed: SPY history unavailable.", file=sys.stderr)
        return 1

    frames: list[pd.DataFrame] = []
    with Storage(settings.db_path) as storage:
        for ticker in tickers:
            prices = _load_prices(price_provider, ticker, start, end)
            if prices.empty:
                continue
            facts = fundamental_provider.get_fundamentals(ticker)
            events = event_records_from_rows(storage.read_events(ticker))

            market_rows: dict[dt.date, dict[str, object]] = {}
            for as_of in _as_of_grid(prices):
                row = build_feature_row(ticker, prices, facts, as_of.date())
                if row is not None:
                    row.update(build_event_features(events, as_of))
                    market_rows[as_of.date()] = row

            labelled = build_short_horizon_training_rows(prices, benchmark, _as_of_grid(prices))
            merged = [
                {**labels.to_dict(), **market_rows.get(labels["as_of"].date(), {})}
                for _, labels in labelled.iterrows()
                if labels["as_of"].date() in market_rows
            ]
            if merged:
                frames.append(pd.DataFrame(merged))
                print(f"  {ticker}: {len(merged)} usable rows")
            else:
                print(f"  {ticker}: skipped (insufficient history)")

    if not frames:
        print("No training rows could be built.", file=sys.stderr)
        return 1

    frame = pd.concat(frames, ignore_index=True)
    print(f"\nTraining frame: {len(frame)} rows, label={label_column}")

    result = ablation_evaluate(frame, label_column=label_column)
    for name, wf in result.items():
        print(
            f"[{name}] folds={wf.folds} rows={wf.rows} "
            f"pos_rate={wf.positive_rate:.2f} "
            f"precision@70={wf.precision_at_70:.2f} recall@70={wf.recall_at_70:.2f} "
            f"brier={wf.brier:.3f}"
        )

    model = train_short_calibrated_model(frame, label_column=label_column)

    if save_path:
        written = save_model(model, save_path)
        print(f"Saved short model to {written} (previous generation kept for rollback).")
    else:
        print("Model NOT saved (pass --save PATH to persist via the registry).")

    logger.info("short-train complete: tickers=%s label=%s", tickers, label_column)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train/evaluate the short-horizon model")
    parser.add_argument("-w", "--watchlist", nargs="+", required=True, help="Tickers to train on.")
    parser.add_argument(
        "--label", choices=("label_1_5d", "label_5_20d"), default="label_5_20d",
        help="Which horizon to train (default: 5-20 trading days).",
    )
    parser.add_argument(
        "--save", metavar="PATH", default=None,
        help=f"Persist the trained model (default: {DEFAULT_SHORT_MODEL_PATH}).",
    )
    args = parser.parse_args(argv)
    return train_short(
        [t.upper() for t in args.watchlist], args.label, args.save or None
    )


# ---------------------------------------------------------------------------
# Paper alerts
# ---------------------------------------------------------------------------


def _news_providers(settings):
    providers = []
    if settings.finnhub_api_key:
        providers.append(FinnhubNewsProvider(settings.finnhub_api_key))
    providers.append(YFinanceNewsProvider())
    return providers


def _to_event_record(item, ticker: str) -> EventRecord:
    published = item.published or dt.datetime.now(dt.UTC)
    if published.tzinfo is None:
        published = published.replace(tzinfo=dt.UTC)
    return EventRecord(
        ticker=ticker.upper(),
        headline=item.headline,
        source=item.source,
        published_at=published,
        known_at=published,  # headline feeds are knowable at publication time
        url=item.url,
        sentiment=item.sentiment,
        category="news",
    )


def ingest_recent_events(storage: Storage, settings, tickers: list[str], lookback_days: int) -> int:
    total = 0
    for provider in _news_providers(settings):
        for ticker in tickers:
            try:
                items = provider.get_news(ticker, lookback_days)
            except Exception as exc:  # noqa: BLE001 — news is best-effort
                print(f"  ! news fetch failed for {ticker} via {provider.name}: {exc}",
                      file=sys.stderr)
                continue
            total += storage.upsert_events([_to_event_record(i, ticker) for i in items])
        print(f"Ingested recent news via {provider.name}.")
    return total


def run_alert_cycle(
    tickers: list[str],
    model_path: str,
    window_hours: int,
    lookback_days: int,
    skip_ingest: bool,
) -> int:
    settings = get_settings()
    model = load_model(model_path)
    if model is None:
        print(
            f"No short model at {model_path} — run stock-monitor-short-train --save first.",
            file=sys.stderr,
        )
        return 1

    price_provider = YFinanceProvider()
    fundamental_provider = EdgarProvider()

    with Storage(settings.db_path) as storage:
        if not skip_ingest:
            ingest_recent_events(storage, settings, tickers, lookback_days)
        fired = run_paper_alerts(
            tickers=tickers,
            prices_provider=lambda t: _load_prices(price_provider, t, 
                dt.date.today() - dt.timedelta(days=400), dt.date.today()),
            fundamentals_provider=fundamental_provider.get_fundamentals,
            storage=storage,
            notifier=get_notifier(settings),
            short_model=model,
            window_hours=window_hours,
        )
    print(f"{len(fired)} alert(s) fired this cycle.")
    for alert in fired:
        print(f"- [{alert.kind}] {alert.ticker}: {alert.title}")
    return 0


def alerts_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one paper-mode short-signal cycle")
    parser.add_argument("-w", "--watchlist", nargs="+", required=True)
    parser.add_argument("--model", default=DEFAULT_SHORT_MODEL_PATH)
    parser.add_argument("--window-hours", type=int, default=24,
                        help="Debounce window per ticker+kind (default: 24h).")
    parser.add_argument("--lookback-days", type=int, default=7,
                        help="How far back to pull fresh headlines (default: 7).")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Score against already-stored events only.")
    args = parser.parse_args(argv)
    return run_alert_cycle(
        [t.upper() for t in args.watchlist],
        args.model,
        args.window_hours,
        args.lookback_days,
        args.skip_ingest,
    )


if __name__ == "__main__":
    raise SystemExit(main())
