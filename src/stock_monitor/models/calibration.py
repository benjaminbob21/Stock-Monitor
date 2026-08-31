"""Confidence calibration (build-plan §1.2, §7 Phase 2).

An uncalibrated score is *confidence theater*: a "78" that isn't right ~78% of the
time is worse than no number. Calibration maps the model's raw probability onto a
histogram-honest one.

Design choice: we keep the **base tree model separate from the calibrator** so SHAP
explanations stay on the real model (the calibrator is a monotonic 1-D transform of
the probability, which would blur attributions if folded in). A ``CalibratedModel``
therefore carries both; the scorer explains with ``base`` and reports the calibrated
probability via ``calibrator``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from stock_monitor.features.builder import FEATURE_COLUMNS


@dataclass
class Calibrator:
    """A fitted 1-D probability calibrator (Platt/sigmoid or isotonic)."""

    method: str  # "sigmoid" | "isotonic"
    estimator: object

    def transform(self, raw: object) -> np.ndarray:
        raw_arr = np.asarray(raw, dtype=float)
        if self.method == "isotonic":
            out = self.estimator.predict(raw_arr)  # type: ignore[attr-defined]
        else:
            out = self.estimator.predict_proba(raw_arr.reshape(-1, 1))[:, 1]  # type: ignore[attr-defined]
        return np.clip(out, 0.0, 1.0)


@dataclass
class CalibratedModel:
    """A base LightGBM classifier plus an optional probability calibrator.

    ``feature_columns`` records the exact feature subset the base model was FIT on
    (LightGBM requires the same columns at predict time). ``None`` means the full
    ``FEATURE_COLUMNS`` set — the pre-subset behaviour, kept for artifacts saved
    before this field existed.
    """

    base: lgb.LGBMClassifier
    calibrator: Calibrator | None = None
    feature_columns: tuple[str, ...] | None = field(default=None)

    @property
    def features(self) -> tuple[str, ...]:
        return self.feature_columns if self.feature_columns is not None else FEATURE_COLUMNS


def fit_calibrator(raw: object, y: object, method: str = "sigmoid") -> Calibrator:
    """Fit a calibrator mapping raw probabilities to calibrated probabilities.

    ``sigmoid`` (Platt) is a single-parameter fit that stays stable on small samples;
    ``isotonic`` is more flexible but needs more data.
    """
    raw_arr = np.asarray(raw, dtype=float)
    y_arr = np.asarray(y, dtype=int)
    if method == "isotonic":
        estimator: object = IsotonicRegression(out_of_bounds="clip")
        estimator.fit(raw_arr, y_arr)  # type: ignore[attr-defined]
    else:
        method = "sigmoid"
        estimator = LogisticRegression()
        estimator.fit(raw_arr.reshape(-1, 1), y_arr)  # type: ignore[attr-defined]
    return Calibrator(method=method, estimator=estimator)
