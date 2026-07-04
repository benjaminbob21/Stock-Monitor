"""LightGBM conviction scorer with SHAP explainability.

Phase 0 keeps the model deliberately small and honest:
- A gradient-boosting classifier predicts P(beat benchmark over the label window).
- The probability becomes a 0-100 conviction score.
- SHAP reports the top-3 drivers behind *every* score — transparency is mandatory,
  never a bare number (build-plan §1.3, risk #4).

The conviction is **uncalibrated** in Phase 0. Calibration (so a "78" is right ~78%
of the time) is Phase 2 work; until then the score is a ranking signal, not a
probability to bet on. That caveat travels with the output.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd
import shap
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from stock_monitor.features.builder import FEATURE_COLUMNS
from stock_monitor.models.calibration import CalibratedModel, Calibrator, fit_calibrator

# Forward-return label window. Locked to 12 months for a cleaner long-term signal
# (build-plan open item #4). Overridable via config for experiments.
LABEL_WINDOW_MONTHS = 12

# A model the scorer understands: a plain tree classifier (uncalibrated) or a
# CalibratedModel (base tree + probability calibrator).
Scoreable = lgb.LGBMClassifier | CalibratedModel


@dataclass(frozen=True)
class Driver:
    """One SHAP contribution behind a score."""

    feature: str
    value: float
    shap: float

    @property
    def direction(self) -> str:
        return "+" if self.shap >= 0 else "-"


@dataclass(frozen=True)
class ScoreResult:
    """A scored ticker with its transparent, PIT-audited context."""

    ticker: str
    conviction: int  # 0-100
    drivers: list[Driver]
    fundamentals_known_on: object | None  # datetime.date | None
    recommendation: str
    calibrated: bool = False


def train_model(frame: pd.DataFrame) -> lgb.LGBMClassifier:
    """Train a small LightGBM classifier on a labelled feature frame."""
    if frame.empty or frame["label"].nunique() < 2:
        raise ValueError(
            "Need a labelled frame with both classes present to train. "
            "Widen the watchlist or history window."
        )

    x = frame[list(FEATURE_COLUMNS)]
    y = frame["label"].astype(int)

    model = lgb.LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=15,
        min_child_samples=5,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=42,
        verbose=-1,
    )
    model.fit(x, y)
    return model


def train_calibrated_model(
    frame: pd.DataFrame, method: str = "sigmoid", cv: int = 3
) -> CalibratedModel:
    """Train the base model and fit a probability calibrator on out-of-fold preds.

    The calibrator is fitted on cross-validated (out-of-fold) probabilities so it
    corrects genuine over/under-confidence rather than memorising the training fit.
    If there isn't enough data to cross-validate, the calibrator is omitted and the
    model degrades gracefully to uncalibrated (honest, not silently wrong).
    """
    base = train_model(frame)
    x = frame[list(FEATURE_COLUMNS)]
    y = frame["label"].astype(int)

    calibrator: Calibrator | None = None
    class_counts = y.value_counts()
    if len(y) >= 2 * cv and class_counts.min() >= cv:
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


def _unwrap(model: Scoreable) -> tuple[lgb.LGBMClassifier, Calibrator | None]:
    if isinstance(model, CalibratedModel):
        return model.base, model.calibrator
    return model, None


def _positive_class_shap(explainer: shap.TreeExplainer, x: pd.DataFrame) -> np.ndarray:
    """Return a 1-D SHAP vector for the positive class of a single row."""
    with warnings.catch_warnings():
        # SHAP emits an informational notice that binary-classifier output is a
        # list of ndarray; the list branch below already handles that shape.
        warnings.simplefilter("ignore", UserWarning)
        values = explainer.shap_values(x)
    if isinstance(values, list):  # older SHAP: [class0, class1]
        values = values[1]
    values = np.asarray(values)
    if values.ndim == 3:  # (rows, features, classes)
        values = values[:, :, -1]
    return values[0]


def score_row(model: Scoreable, row: dict[str, object]) -> ScoreResult:
    """Score one PIT feature row and explain it with the top-3 SHAP drivers.

    If ``model`` is a CalibratedModel, the conviction is the *calibrated* probability
    while SHAP still explains the underlying tree model.
    """
    base, calibrator = _unwrap(model)
    x = pd.DataFrame([{f: row.get(f) for f in FEATURE_COLUMNS}], columns=list(FEATURE_COLUMNS))

    raw = float(base.predict_proba(x)[0, 1])
    proba = float(calibrator.transform([raw])[0]) if calibrator is not None else raw
    conviction = int(round(proba * 100))

    explainer = shap.TreeExplainer(base)
    shap_vec = _positive_class_shap(explainer, x)

    ranked = sorted(
        (
            Driver(feature=f, value=_as_float(row.get(f)), shap=float(s))
            for f, s in zip(FEATURE_COLUMNS, shap_vec, strict=True)
        ),
        key=lambda d: abs(d.shap),
        reverse=True,
    )

    return ScoreResult(
        ticker=str(row.get("ticker", "?")),
        conviction=conviction,
        drivers=ranked[:3],
        fundamentals_known_on=row.get("fundamentals_known_on"),
        recommendation=recommendation_band(conviction),
        calibrated=calibrator is not None,
    )


def recommendation_band(conviction: int) -> str:
    """Map a conviction score to a human-in-the-loop suggestion (never an order)."""
    if conviction >= 70:
        return "consider buying"
    if conviction >= 55:
        return "lean buy / watch"
    if conviction >= 45:
        return "hold / neutral"
    if conviction >= 30:
        return "lean trim / watch"
    return "consider trimming / avoid"


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
