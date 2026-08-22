"""Model persistence + versioning (build-plan §1.6: reproducibility for cheap).

Every score must be traceable to *which* model produced it. We derive a stable
``model_version`` from the feature set + hyperparameters so identical configs hash
identically, and persist the fitted model with joblib for the API to load.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def save_model(model: Scoreable, path: str) -> str:
    """Version and promote a model while retaining a one-generation rollback copy."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    version = compute_model_version(model)
    versioned = target.with_name(f"{target.stem}.{version}{target.suffix}")
    previous = target.with_name(f"{target.stem}.previous{target.suffix}")
    manifest = target.with_name(f"{target.stem}.manifest.json")

    with tempfile.NamedTemporaryFile(
        dir=target.parent, suffix=target.suffix, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        joblib.dump(model, temporary)
        os.replace(temporary, versioned)
    finally:
        temporary.unlink(missing_ok=True)

    if target.exists():
        _atomic_copy(target, previous)
    _atomic_copy(versioned, target)
    metadata = {
        "model_version": version,
        "active_artifact": versioned.name,
        "previous_artifact": previous.name if previous.exists() else None,
        "sha256": _sha256(versioned),
        "saved_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return str(target)


def restore_previous_model(path: str) -> str:
    """Restore the last promoted model; raise ``FileNotFoundError`` if unavailable."""
    target = Path(path)
    previous = target.with_name(f"{target.stem}.previous{target.suffix}")
    if not previous.exists():
        raise FileNotFoundError(f"no previous model artifact exists for {target}")
    _atomic_copy(previous, target)
    return str(target)


def read_model_manifest(path: str) -> dict[str, object] | None:
    """Read promotion metadata, returning ``None`` when no manifest exists."""
    manifest = Path(path).with_name(f"{Path(path).stem}.manifest.json")
    if not manifest.exists():
        return None
    return json.loads(manifest.read_text(encoding="utf-8"))


def load_model(path: str) -> Scoreable | None:
    """Load a persisted model, or ``None`` if it doesn't exist."""
    if not Path(path).exists():
        return None
    return joblib.load(path)
