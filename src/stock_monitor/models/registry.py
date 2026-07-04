"""Model persistence + versioning (build-plan §1.6: reproducibility for cheap).

Every score must be traceable to *which* model produced it. We derive a stable
``model_version`` from the feature set + hyperparameters so identical configs hash
identically, and persist the fitted model with joblib for the API to load.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import lightgbm as lgb

from stock_monitor.features.builder import FEATURE_COLUMNS
from stock_monitor.models.calibration import CalibratedModel

Scoreable = lgb.LGBMClassifier | CalibratedModel


def _base_and_calibration(model: Scoreable) -> tuple[lgb.LGBMClassifier, str]:
    if isinstance(model, CalibratedModel):
        method = model.calibrator.method if model.calibrator is not None else "none"
        return model.base, method
    return model, "none"


def compute_model_version(model: Scoreable, extra: str = "") -> str:
    """Return a short, stable version string for a fitted (possibly calibrated) model."""
    base, calibration = _base_and_calibration(model)
    payload = json.dumps(
        {
            "features": list(FEATURE_COLUMNS),
            "params": {k: str(v) for k, v in sorted(base.get_params().items())},
            "calibration": calibration,
            "extra": extra,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"lgbm-{digest}"


def save_model(model: Scoreable, path: str) -> str:
    """Persist a fitted model to ``path`` (parent dirs created as needed)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
    return path


def load_model(path: str) -> Scoreable | None:
    """Load a persisted model, or ``None`` if it doesn't exist."""
    if not Path(path).exists():
        return None
    return joblib.load(path)
