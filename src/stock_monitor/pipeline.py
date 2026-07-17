"""Training pipeline: ingest -> validate -> store -> train -> MLflow -> persist.

Run with ``stock-monitor-train``. This is the batch job that produces the model the
API serves. Every run is logged to MLflow (params, metrics, quarantine rate) and the
fitted model is persisted so ``GET /score/{ticker}`` can load it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from dataclasses import dataclass

import mlflow
import pandas as pd

from stock_monitor.backfill import make_sentiment_lookup
from stock_monitor.config import Settings, get_settings
from stock_monitor.features.builder import FEATURE_COLUMNS, build_training_frame
from stock_monitor.features.schema import ValidationReport, validate_features
from stock_monitor.macro import make_macro_lookup
from stock_monitor.models.registry import compute_model_version, save_model
from stock_monitor.models.scorer import SHORT_HORIZON_LGBM_PARAMS, train_calibrated_model
from stock_monitor.providers import get_price_provider
from stock_monitor.providers.base import FundamentalProvider, PriceProvider
from stock_monitor.providers.edgar_provider import EdgarProvider
from stock_monitor.storage.db import Storage

DEFAULT_WATCHLIST: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "JPM", "XOM", "KO")
BENCHMARK = "SPY"
HISTORY_YEARS = 8


@dataclass(frozen=True)
class TrainingResult:
    model_version: str
    rows_trained: int
    positive_rate: float
    train_accuracy: float
    calibration: str
    report: ValidationReport
    model_path: str


def assemble_training_frame(
    watchlist: list[str],
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    label_window_months: int,
    history_years: int = HISTORY_YEARS,
) -> pd.DataFrame:
    """Fetch prices + PIT fundamentals per ticker and pool their labelled frames."""
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * history_years)

    benchmark = price_provider.get_prices(BENCHMARK, start, end)
    if benchmark.empty:
        raise RuntimeError(f"benchmark {BENCHMARK} price history unavailable")

    frames: list[pd.DataFrame] = []
    for ticker in watchlist:
        prices = price_provider.get_prices(ticker, start, end)
        if prices.empty:
            continue
        facts = fundamental_provider.get_fundamentals(ticker)
        frame = build_training_frame(
            ticker, prices, facts, benchmark, label_window_months
        )
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def assemble_training_frames(
    watchlist: list[str],
    price_provider: PriceProvider,
    fundamental_provider: FundamentalProvider,
    horizons: list[int],
    history_years: int = HISTORY_YEARS,
    storage: Storage | None = None,
) -> dict[int, pd.DataFrame]:
    """Fetch each ticker once and build a labelled frame per horizon (no double fetch).

    When ``storage`` is provided, each ticker's backfilled daily news sentiment is read
    from ``news_sentiment`` and baked into the ``sentiment`` feature PIT-correctly; with
    no storage (or no stored news for a ticker) ``sentiment`` stays 0.0 — the
    pre-backfill behaviour. Macro/regime features are read once from ``macro_series`` and
    shared across every ticker (they're ticker-independent) with the same PIT guarantee.
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * history_years)

    benchmark = price_provider.get_prices(BENCHMARK, start, end)
    if benchmark.empty:
        raise RuntimeError(f"benchmark {BENCHMARK} price history unavailable")

    # Macro is the same for all tickers on a given as_of, so build the lookup once.
    macro_lookup = None
    if storage is not None:
        macro = storage.read_macro_series()
        if not macro.empty:
            macro_lookup = make_macro_lookup(macro)

    by_horizon: dict[int, list[pd.DataFrame]] = {h: [] for h in horizons}
    for ticker in watchlist:
        prices = price_provider.get_prices(ticker, start, end)
        if prices.empty:
            continue
        facts = fundamental_provider.get_fundamentals(ticker)
        sentiment_lookup = None
        if storage is not None:
            daily = storage.read_news_sentiment(ticker)
            if not daily.empty:
                sentiment_lookup = make_sentiment_lookup(daily)
        for horizon in horizons:
            frame = build_training_frame(
                ticker, prices, facts, benchmark, horizon,
                sentiment_lookup=sentiment_lookup,
                macro_lookup=macro_lookup,
            )
            if not frame.empty:
                by_horizon[horizon].append(frame)

    return {
        h: pd.concat(v, ignore_index=True) if v else pd.DataFrame()
        for h, v in by_horizon.items()
    }


def _log_mlflow(
    frame: pd.DataFrame,
    report: ValidationReport,
    version: str,
    accuracy: float,
    settings: Settings,
    params: dict,
    calibration: str,
) -> None:
    # MLflow 3.x gates the local file store behind an opt-in; mlruns/ is gitignored
    # and the right weight for a solo project (build-plan §4).
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment)
    with mlflow.start_run():
        mlflow.set_tag("model_version", version)
        mlflow.log_params(params)
        mlflow.log_param("label_window_months", settings.label_window_months)
        mlflow.log_param("calibration", calibration)
        mlflow.log_param("features", ",".join(FEATURE_COLUMNS))
        mlflow.log_metrics(
            {
                "rows_trained": float(len(frame)),
                "positive_rate": float(frame["label"].mean()),
                "quarantine_rate": report.quarantine_rate,
                "train_accuracy": accuracy,
            }
        )
        mlflow.log_artifact(settings.model_path)


def _training_price_provider(settings: Settings) -> PriceProvider:
    """Price source for TRAINING: the persistent cache (read-only) when enabled.

    Reading purely from the local cache means a retrain makes zero upstream requests
    (so it can never hit Tiingo's free-tier rate limit) and always trains on the exact
    prices we already vetted and stored. The cache is kept current by the daily append
    job; if it's empty, fill it first (``scripts/fill_price_cache.py``).
    """
    upstream = get_price_provider(settings)
    if not settings.use_price_cache:
        return upstream
    from stock_monitor.providers.price_cache import CachedPriceProvider, PriceCache

    return CachedPriceProvider(
        upstream, PriceCache(settings.price_cache_path), fetch_missing=False
    )


def run_training(
    watchlist: list[str],
    settings: Settings | None = None,
    price_provider: PriceProvider | None = None,
    fundamental_provider: FundamentalProvider | None = None,
    log_mlflow: bool = True,
) -> TrainingResult:
    """Run the full pipeline and return a summary. Providers are injectable for tests."""
    settings = settings or get_settings()
    price_provider = price_provider or _training_price_provider(settings)
    fundamental_provider = fundamental_provider or EdgarProvider()

    long_h = settings.label_window_months
    short_h = settings.label_window_months_short
    with Storage(settings.db_path) as store:
        frames = assemble_training_frames(
            watchlist,
            price_provider,
            fundamental_provider,
            [long_h, short_h],
            history_years=settings.training_history_years,
            storage=store,
        )
        pooled = frames.get(long_h, pd.DataFrame())
        if pooled.empty:
            raise RuntimeError("no labelled training data could be assembled")

        valid, quarantined, report = validate_features(pooled)

        store.upsert_features(valid)
        store.record_quarantine(quarantined)

    model = train_calibrated_model(valid)
    version = compute_model_version(model)
    save_model(model, settings.model_path)

    # Secondary short-horizon model (near-term read). Best-effort: if its data is thin
    # or single-class, skip it — the primary model still ships. Trained with heavier
    # regularization so the 3-month signal stops saturating and flattening to base rate.
    short_pooled = frames.get(short_h, pd.DataFrame())
    if not short_pooled.empty:
        valid_short, _, _ = validate_features(short_pooled)
        try:
            short_model = train_calibrated_model(
                valid_short, params=SHORT_HORIZON_LGBM_PARAMS
            )
            save_model(short_model, settings.model_path_short)
        except ValueError:
            pass

    x = valid[list(FEATURE_COLUMNS)]
    accuracy = float((model.base.predict(x) == valid["label"].astype(int)).mean())
    calibration = model.calibrator.method if model.calibrator is not None else "none"

    if log_mlflow:
        _log_mlflow(
            valid, report, version, accuracy, settings, model.base.get_params(), calibration
        )

    return TrainingResult(
        model_version=version,
        rows_trained=len(valid),
        positive_rate=float(valid["label"].mean()),
        train_accuracy=accuracy,
        calibration=calibration,
        report=report,
        model_path=settings.model_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stock-Monitor training pipeline")
    parser.add_argument(
        "-w", "--watchlist", nargs="+", default=list(DEFAULT_WATCHLIST),
        help="Tickers to train on.",
    )
    parser.add_argument(
        "--no-mlflow", action="store_true", help="Skip MLflow logging."
    )
    args = parser.parse_args(argv)

    result = run_training(
        [t.upper() for t in args.watchlist], log_mlflow=not args.no_mlflow
    )
    print(
        f"Trained {result.model_version} on {result.rows_trained} rows "
        f"(positive_rate={result.positive_rate:.2f}, "
        f"in-sample_acc={result.train_accuracy:.2f}, "
        f"calibration={result.calibration}, "
        f"quarantined={result.report.quarantined}/{result.report.total}).\n"
        f"Model saved to {result.model_path}. In-sample accuracy is NOT validation "
        f"— run `stock-monitor-validate` for honest walk-forward + calibration metrics."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
