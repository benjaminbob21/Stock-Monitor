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

from stock_monitor.config import Settings, get_settings
from stock_monitor.features.builder import FEATURE_COLUMNS, build_training_frame
from stock_monitor.features.schema import ValidationReport, validate_features
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
) -> pd.DataFrame:
    """Fetch prices + PIT fundamentals per ticker and pool their labelled frames."""
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * HISTORY_YEARS)

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
) -> dict[int, pd.DataFrame]:
    """Fetch each ticker once and build a labelled frame per horizon (no double fetch)."""
    end = dt.date.today()
    start = end - dt.timedelta(days=365 * HISTORY_YEARS)

    benchmark = price_provider.get_prices(BENCHMARK, start, end)
    if benchmark.empty:
        raise RuntimeError(f"benchmark {BENCHMARK} price history unavailable")

    by_horizon: dict[int, list[pd.DataFrame]] = {h: [] for h in horizons}
    for ticker in watchlist:
        prices = price_provider.get_prices(ticker, start, end)
        if prices.empty:
            continue
        facts = fundamental_provider.get_fundamentals(ticker)
        for horizon in horizons:
            frame = build_training_frame(ticker, prices, facts, benchmark, horizon)
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


def run_training(
    watchlist: list[str],
    settings: Settings | None = None,
    price_provider: PriceProvider | None = None,
    fundamental_provider: FundamentalProvider | None = None,
    log_mlflow: bool = True,
) -> TrainingResult:
    """Run the full pipeline and return a summary. Providers are injectable for tests."""
    settings = settings or get_settings()
    price_provider = price_provider or get_price_provider(settings)
    fundamental_provider = fundamental_provider or EdgarProvider()

    long_h = settings.label_window_months
    short_h = settings.label_window_months_short
    frames = assemble_training_frames(
        watchlist, price_provider, fundamental_provider, [long_h, short_h]
    )
    pooled = frames.get(long_h, pd.DataFrame())
    if pooled.empty:
        raise RuntimeError("no labelled training data could be assembled")

    valid, quarantined, report = validate_features(pooled)

    with Storage(settings.db_path) as store:
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
