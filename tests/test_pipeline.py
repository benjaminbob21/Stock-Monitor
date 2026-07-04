"""Training pipeline test (network-free, MLflow disabled)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stock_monitor.config import Settings
from stock_monitor.pipeline import run_training
from stock_monitor.storage import Storage


def test_run_training_persists_model_and_stores_features(
    world: SimpleNamespace, tmp_path: Path
) -> None:
    settings = Settings(
        db_path=str(tmp_path / "test.duckdb"),
        model_path=str(tmp_path / "model.joblib"),
    )

    result = run_training(
        [world.ticker],
        settings=settings,
        price_provider=world.price_provider,
        fundamental_provider=world.fundamental_provider,
        log_mlflow=False,
    )

    assert result.rows_trained > 0
    assert 0.0 <= result.positive_rate <= 1.0
    assert result.model_version.startswith("lgbm-")
    assert Path(settings.model_path).exists()

    with Storage(settings.db_path) as store:
        assert store.count("features") > 0
