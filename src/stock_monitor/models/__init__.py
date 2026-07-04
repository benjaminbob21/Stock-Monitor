"""Models (training, scoring, calibration, explainability)."""

from stock_monitor.models.calibration import CalibratedModel, Calibrator, fit_calibrator
from stock_monitor.models.scorer import (
    LABEL_WINDOW_MONTHS,
    Driver,
    ScoreResult,
    predict_conviction,
    recommendation_band,
    score_row,
    train_calibrated_model,
    train_model,
)

__all__ = [
    "LABEL_WINDOW_MONTHS",
    "CalibratedModel",
    "Calibrator",
    "Driver",
    "ScoreResult",
    "fit_calibrator",
    "predict_conviction",
    "recommendation_band",
    "score_row",
    "train_calibrated_model",
    "train_model",
]
