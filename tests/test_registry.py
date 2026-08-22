"""Model artifact versioning and rollback tests."""

from pathlib import Path

import lightgbm as lgb

from stock_monitor.models.registry import (
    load_model,
    read_model_manifest,
    restore_previous_model,
    save_model,
)


def _model(n_estimators: int) -> lgb.LGBMClassifier:
    return lgb.LGBMClassifier(n_estimators=n_estimators, random_state=7)


def test_save_model_versions_and_preserves_previous(tmp_path: Path) -> None:
    path = str(tmp_path / "latest.joblib")
    save_model(_model(10), path)
    first = load_model(path)
    assert first is not None

    save_model(_model(20), path)
    active = load_model(path)
    manifest = read_model_manifest(path)
    assert active is not None
    assert active.get_params()["n_estimators"] == 20
    assert manifest is not None
    assert manifest["active_artifact"].startswith("latest.lgbm-")
    assert Path(path).with_name("latest.previous.joblib").exists()
    assert len(list(tmp_path.glob("latest.lgbm-*.joblib"))) == 2

    restore_previous_model(path)
    restored = load_model(path)
    assert restored is not None
    assert restored.get_params()["n_estimators"] == 10


def test_restore_previous_requires_a_prior_promotion(tmp_path: Path) -> None:
    path = str(tmp_path / "latest.joblib")
    try:
        restore_previous_model(path)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("restore should fail when no previous artifact exists")
