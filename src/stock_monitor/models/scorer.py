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

from stock_monitor.features.builder import FEATURE_COLUMNS

# Forward-return label window. Locked to 12 months for a cleaner long-term signal
# (build-plan open item #4). Overridable via config for experiments.
LABEL_WINDOW_MONTHS = 12


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
    conviction: int  # 0-100 (uncalibrated in Phase 0)
    drivers: list[Driver]
    fundamentals_known_on: object | None  # datetime.date | None
    recommendation: str


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


def score_row(model: lgb.LGBMClassifier, row: dict[str, object]) -> ScoreResult:
    """Score one PIT feature row and explain it with the top-3 SHAP drivers."""
    x = pd.DataFrame([{f: row.get(f) for f in FEATURE_COLUMNS}], columns=list(FEATURE_COLUMNS))

    proba = float(model.predict_proba(x)[0, 1])
    conviction = int(round(proba * 100))

    explainer = shap.TreeExplainer(model)
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
