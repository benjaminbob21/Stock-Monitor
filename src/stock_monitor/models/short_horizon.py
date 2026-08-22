"""Short-horizon (1-4 week) event-driven model training + walk-forward evaluation.

This is a *distinct* model from the long (12-month) and medium (3-month) horizons:
it consumes the PIT event features plus a small daily market subset and answers the
short-term question *"does recent news/price action suggest a beat-the-benchmark move
in the next 1-4 weeks?"* It never replaces the primary model.

Leakage rules:
- Training is evaluated only by **expanding-window temporal splits** on ``as_of`` —
  never random CV, which would leak future rows into the past (horizon-mixing).
- Both labels and features are point-in-time by construction (see
  ``features.labels`` and ``features.events``); this module only *consumes* them.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd

from stock_monitor.features.events import EVENT_FEATURE_COLUMNS
from stock_monitor.models.calibration import CalibratedModel, Calibrator, fit_calibrator

# Market features available on the daily as-of grid (fundamental quarterly features are
# deliberately excluded — the short-horizon model should react to price + news only).
SHORT_MARKET_FEATURES: tuple[str, ...] = (
    "mom_12_1",
    "mom_6_1",
    "vol_3m",
    "rsi_14",
    "trend_200",
    "sentiment",
)

SHORT_FEATURE_COLUMNS: tuple[str, ...] = (*SHORT_MARKET_FEATURES, *EVENT_FEATURE_COLUMNS)

SHORT_HORIZON_LABELS: tuple[str, ...] = ("label_1_5d", "label_5_20d")

# Heavier regularization than even the 3-month short model: a 1-4 week beat-the-
# benchmark target is close to noise, so small capacity + strong L1/L2 prevents the
# memorization that collapses calibration into a flat base rate.
SHORT_HORIZON_LGBM_PARAMS: dict = {
    "n_estimators": 80,
    "learning_rate": 0.03,
    "num_leaves": 5,
    "min_child_samples": 40,
    "subsample": 0.6,
    "colsample_bytree": 0.6,
    "reg_alpha": 1.0,
    "reg_lambda": 2.0,
    "random_state": 42,
    "verbose": -1,
}

_DEFAULT_CALIBRATION_METHOD = "sigmoid"
_DEFAULT_CV = 3

Scoreable = lgb.LGBMClassifier | CalibratedModel


@dataclass(frozen=True)
class WalkForwardResult:
    """Temporal walk-forward evaluation metrics for one horizon."""

    folds: int
    rows: int
    positive_rate: float
    precision_at_70: float
    recall_at_70: float
    n_positives: int
    brier: float
    mean_conviction: float


def train_short_horizon_model(
    frame: pd.DataFrame,
    label_column: str = "label_1_5d",
    params: dict | None = None,
) -> lgb.LGBMClassifier:
    """Train the raw short-horizon LightGBM base (uncalibrated)."""
    if label_column not in SHORT_HORIZON_LABELS:
        raise ValueError(f"unknown short label column: {label_column}")
    if frame.empty or frame[label_column].nunique() < 2:
        raise ValueError("short-horizon frame must contain both label classes")

    x = frame[list(SHORT_FEATURE_COLUMNS)]
    y = frame[label_column].astype(int)
    model = lgb.LGBMClassifier(**{**SHORT_HORIZON_LGBM_PARAMS, **(params or {})})
    model.fit(x, y)
    return model


def train_short_calibrated_model(
    frame: pd.DataFrame,
    label_column: str = "label_1_5d",
    method: str = _DEFAULT_CALIBRATION_METHOD,
    cv: int = _DEFAULT_CV,
    params: dict | None = None,
) -> CalibratedModel:
    """Train the short base + calibrator, degrading gracefully to uncalibrated."""
    base = train_short_horizon_model(frame, label_column, params=params)
    x = frame[list(SHORT_FEATURE_COLUMNS)]
    y = frame[label_column].astype(int)

    calibrator: Calibrator | None = None
    class_counts = y.value_counts()
    if len(y) >= 2 * cv and class_counts.min() >= cv:
        from sklearn.model_selection import StratifiedKFold, cross_val_predict

        try:
            oof = cross_val_predict(
                lgb.LGBMClassifier(**base.get_params()),
                x,
                y,
                cv=StratifiedKFold(n_splits=cv, shuffle=True, random_state=42),
                method="predict_proba",
            )[:, 1]
            calibrator = fit_calibrator(oof, y, method=method)
        except (ValueError, IndexError):
            calibrator = None

    return CalibratedModel(base=base, calibrator=calibrator)


def predict_short_conviction(model: Scoreable, row: dict[str, object]) -> int:
    """Return the 0-100 short-horizon conviction for one feature row."""
    base, calibrator = _unwrap(model)
    x = pd.DataFrame(
        [{f: row.get(f) for f in SHORT_FEATURE_COLUMNS}],
        columns=list(SHORT_FEATURE_COLUMNS),
    )
    raw = float(np.asarray(base.predict_proba(x))[0, 1])
    proba = float(calibrator.transform([raw])[0]) if calibrator is not None else raw
    return int(round(proba * 100))


def _unwrap(model: Scoreable) -> tuple[lgb.LGBMClassifier, Calibrator | None]:
    if isinstance(model, CalibratedModel):
        return model.base, model.calibrator
    return model, None


def temporal_splits(
    frame: pd.DataFrame, label_column: str, n_splits: int = 5
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Return expanding-window (train, test) pairs, strictly by ``as_of``.

    Each split trains only on rows whose ``as_of`` predates the entire test chunk,
    so no test row can ever influence training. Splits that lack both classes in the
    test chunk are skipped.

    ``label_column`` selects which short-horizon label (e.g. ``label_1_5d``) classes
    are checked — there is no generic ``label`` column on short-horizon frames.
    """
    if n_splits < 2 or frame.empty:
        return []
    ordered = frame.sort_values("as_of").reset_index(drop=True)
    ordered = ordered.assign(as_of=pd.to_datetime(ordered["as_of"]))
    dates = pd.unique(ordered["as_of"])
    if len(dates) <= n_splits:
        return []

    chunks = np.array_split(np.sort(np.array(dates)), n_splits)
    folds: list[tuple[pd.DataFrame, pd.DataFrame]] = []
    for i in range(1, n_splits):
        chunk_start = pd.Timestamp(np.min(chunks[i]))
        train = ordered[ordered["as_of"] < chunk_start]
        test = ordered[ordered["as_of"] >= chunk_start].loc[
            ordered["as_of"] >= chunk_start
        ]
        test = test[test["as_of"].isin(chunks[i])]
        if train.empty or test.empty or test[label_column].nunique() < 2:
            continue
        folds.append((train, test))
    return folds


def walk_forward_evaluate(
    frame: pd.DataFrame,
    label_column: str = "label_1_5d",
    n_splits: int = 5,
    feature_columns: tuple[str, ...] = SHORT_FEATURE_COLUMNS,
) -> WalkForwardResult:
    """Walk-forward out-of-sample metrics using only temporal training splits.

    Conviction = calibrated 0-100 probability (uncalibrated raw when no calibrator).
    ``precision_at_70`` / ``recall_at_70`` treat >= 70 as a "candidate" long signal.
    """
    folds = temporal_splits(frame, label_column, n_splits)
    if not folds:
        return WalkForwardResult(
            folds=0, rows=0, positive_rate=0.0, precision_at_70=0.0,
            recall_at_70=0.0, n_positives=0, brier=float("nan"),
            mean_conviction=float("nan"),
        )

    convictions: list[int] = []
    labels: list[int] = []
    for train, test in folds:
        model = train_short_calibrated_model(train, label_column=label_column)
        for _, row in test.iterrows():
            convictions.append(predict_short_conviction(model, row.to_dict()))
            labels.append(int(row[label_column]))

    conviction_arr = np.asarray(convictions, dtype=float)
    label_arr = np.asarray(labels, dtype=float)
    positive_mask = label_arr == 1.0
    candidate_mask = conviction_arr >= 70.0
    n_positives = int(candidate_mask.sum())
    precision = (
        float((candidate_mask & positive_mask).sum() / n_positives)
        if n_positives
        else float("nan")
    )
    recall = (
        float((candidate_mask & positive_mask).sum() / positive_mask.sum())
        if positive_mask.sum()
        else float("nan")
    )
    return WalkForwardResult(
        folds=len(folds),
        rows=len(convictions),
        positive_rate=float(positive_mask.mean()),
        precision_at_70=precision,
        recall_at_70=recall,
        n_positives=n_positives,
        brier=float(np.mean((conviction_arr / 100.0 - label_arr) ** 2)),
        mean_conviction=float(conviction_arr.mean()),
    )


def ablation_evaluate(
    frame: pd.DataFrame,
    label_column: str = "label_1_5d",
    n_splits: int = 5,
) -> dict[str, WalkForwardResult]:
    """Walk-forward results for market-only vs market+event feature sets."""
    return {
        "market_only": walk_forward_evaluate(
            frame, label_column, n_splits, SHORT_MARKET_FEATURES
        ),
        "market_and_events": walk_forward_evaluate(frame, label_column, n_splits),
    }