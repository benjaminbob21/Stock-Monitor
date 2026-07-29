"""Walk-forward (time-aware) validation with calibration metrics (build-plan §7 Phase 2).

The one rule that makes a stock backtest honest: **never let the model see the
future.** This module does time-ordered, *purged* walk-forward evaluation:

- Folds are contiguous slices of the timeline; each fold trains on the past and
  tests on the next slice (expanding window).
- Because a label looks forward ``embargo_months``, training rows whose label window
  would overlap the test period are **purged** (dropped). This kills the subtle
  leakage where a training label already "knows" the test period's outcome.

For every fold we fit the model, calibrate on the training fold, and score the test
fold. Pooling the out-of-fold predictions gives an honest **Brier score** (raw vs
calibrated), accuracy, AUC, and a reliability curve.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score

from stock_monitor.features.builder import FEATURE_COLUMNS
from stock_monitor.models.calibration import fit_calibrator
from stock_monitor.models.scorer import LABEL_WINDOW_MONTHS, train_model


@dataclass(frozen=True)
class FoldResult:
    fold: int
    n_train: int
    n_test: int
    brier_raw: float
    brier_calibrated: float
    accuracy: float
    auc: float | None


@dataclass(frozen=True)
class WalkForwardReport:
    folds: list[FoldResult]
    n: int
    hit_rate: float
    brier_raw: float
    brier_calibrated: float
    accuracy: float
    auc: float | None
    reliability: list[tuple[float, float]]  # (mean_predicted, observed_fraction)


def walk_forward_folds(
    as_of: pd.Series, n_splits: int = 4, embargo_months: int = LABEL_WINDOW_MONTHS
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Yield (train_idx, test_idx) pairs: expanding window, purged by an embargo."""
    ts = pd.to_datetime(pd.Series(as_of).reset_index(drop=True))
    unique = np.array(sorted(ts.dropna().unique()))
    if len(unique) < 2:
        return []
    n_splits = max(1, min(n_splits, len(unique) - 1))
    segments = np.array_split(unique, n_splits + 1)

    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(n_splits):
        train_dates = np.concatenate(segments[: k + 1])
        test_dates = segments[k + 1]
        if len(test_dates) == 0:
            continue
        test_start = pd.Timestamp(test_dates.min())
        embargo_cut = test_start - pd.DateOffset(months=embargo_months)

        train_mask = ts.isin(train_dates) & (ts <= embargo_cut)
        test_mask = ts.isin(test_dates)
        train_idx = np.where(train_mask.to_numpy())[0]
        test_idx = np.where(test_mask.to_numpy())[0]
        if len(train_idx) and len(test_idx):
            folds.append((train_idx, test_idx))
    return folds


def evaluate_walk_forward(
    frame: pd.DataFrame,
    n_splits: int = 4,
    embargo_months: int = LABEL_WINDOW_MONTHS,
    calibration_method: str = "sigmoid",
) -> WalkForwardReport:
    """Run purged walk-forward evaluation and return calibration-aware metrics."""
    frame = frame.reset_index(drop=True)
    folds = walk_forward_folds(frame["as_of"], n_splits, embargo_months)

    oof_y: list[int] = []
    oof_raw: list[float] = []
    oof_cal: list[float] = []
    fold_results: list[FoldResult] = []

    for i, (train_idx, test_idx) in enumerate(folds):
        y_train = frame.loc[train_idx, "label"].astype(int)
        if y_train.nunique() < 2:
            continue  # can't train a classifier on one class

        model = train_model(frame.loc[train_idx])
        x_train = frame.loc[train_idx, list(FEATURE_COLUMNS)]
        x_test = frame.loc[test_idx, list(FEATURE_COLUMNS)]
        y_test = frame.loc[test_idx, "label"].astype(int)

        raw_train = np.asarray(model.predict_proba(x_train))[:, 1]
        raw_test = np.asarray(model.predict_proba(x_test))[:, 1]
        calibrator = fit_calibrator(raw_train, y_train, method=calibration_method)
        cal_test = calibrator.transform(raw_test)

        both_classes = y_test.nunique() > 1
        fold_results.append(
            FoldResult(
                fold=i,
                n_train=len(train_idx),
                n_test=len(test_idx),
                brier_raw=float(brier_score_loss(y_test, raw_test)),
                brier_calibrated=float(brier_score_loss(y_test, cal_test)),
                accuracy=float(accuracy_score(y_test, (cal_test >= 0.5).astype(int))),
                auc=float(roc_auc_score(y_test, cal_test)) if both_classes else None,
            )
        )
        oof_y.extend(y_test.tolist())
        oof_raw.extend(raw_test.tolist())
        oof_cal.extend(cal_test.tolist())

    if not oof_y:
        raise ValueError(
            "Walk-forward produced no evaluable folds — need more history/tickers "
            "(each fold must train on both classes after purging)."
        )

    y = np.array(oof_y)
    raw = np.array(oof_raw)
    cal = np.array(oof_cal)
    multiclass = len(np.unique(y)) > 1

    reliability: list[tuple[float, float]] = []
    if multiclass:
        n_bins = min(5, max(2, len(y) // 10))
        observed, predicted = calibration_curve(y, cal, n_bins=n_bins, strategy="quantile")
        reliability = [(float(p), float(o)) for p, o in zip(predicted, observed, strict=False)]

    return WalkForwardReport(
        folds=fold_results,
        n=len(y),
        hit_rate=float(y.mean()),
        brier_raw=float(brier_score_loss(y, raw)),
        brier_calibrated=float(brier_score_loss(y, cal)),
        accuracy=float(accuracy_score(y, (cal >= 0.5).astype(int))),
        auc=float(roc_auc_score(y, cal)) if multiclass else None,
        reliability=reliability,
    )


def _format_report(report: WalkForwardReport) -> str:
    lines = [
        "=" * 70,
        "WALK-FORWARD VALIDATION (purged, time-aware) — Phase 2 trust check",
        "=" * 70,
        f"out-of-fold samples : {report.n}  (base rate / hit-rate: {report.hit_rate:.2%})",
        f"Brier  raw -> calib : {report.brier_raw:.4f} -> {report.brier_calibrated:.4f} "
        "(lower is better)",
        f"accuracy (calib)    : {report.accuracy:.2%}",
        f"AUC (calib)         : {report.auc:.3f}" if report.auc is not None else "AUC: n/a",
        "",
        "reliability (mean predicted -> observed positive fraction):",
    ]
    for predicted, observed in report.reliability:
        lines.append(f"  {predicted:.2f} -> {observed:.2f}")
    lines.append("")
    lines.append("per fold:")
    for f in report.folds:
        auc = f"{f.auc:.3f}" if f.auc is not None else "n/a"
        lines.append(
            f"  fold {f.fold}: train={f.n_train:>4} test={f.n_test:>4} "
            f"brier {f.brier_raw:.3f}->{f.brier_calibrated:.3f} "
            f"acc {f.accuracy:.2%} auc {auc}"
        )
    lines.append("")
    lines.append(
        "Note: metrics are only as trustworthy as the sample size. A small watchlist "
        "yields noisy folds — widen the universe (Phase 3) for firmer numbers."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from stock_monitor.config import get_settings
    from stock_monitor.features.schema import validate_features
    from stock_monitor.pipeline import DEFAULT_WATCHLIST, assemble_training_frame
    from stock_monitor.providers.edgar_provider import EdgarProvider
    from stock_monitor.providers.yfinance_provider import YFinanceProvider

    parser = argparse.ArgumentParser(description="Stock-Monitor walk-forward validation")
    parser.add_argument("-w", "--watchlist", nargs="+", default=list(DEFAULT_WATCHLIST))
    parser.add_argument("--splits", type=int, default=4)
    parser.add_argument("--method", choices=["sigmoid", "isotonic"], default="sigmoid")
    args = parser.parse_args(argv)

    settings = get_settings()
    frame = assemble_training_frame(
        [t.upper() for t in args.watchlist],
        YFinanceProvider(),
        EdgarProvider(),
        settings.label_window_months,
    )
    valid, _, _ = validate_features(frame)
    report = evaluate_walk_forward(
        valid,
        n_splits=args.splits,
        embargo_months=settings.label_window_months,
        calibration_method=args.method,
    )
    print(_format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
