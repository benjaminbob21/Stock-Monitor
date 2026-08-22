"""Short-horizon model + walk-forward tests (network-free, synthetic data)."""

import pandas as pd

from stock_monitor.features.events import EVENT_FEATURE_COLUMNS
from stock_monitor.models.calibration import CalibratedModel
from stock_monitor.models.short_horizon import (
    SHORT_FEATURE_COLUMNS,
    SHORT_HORIZON_LABELS,
    ablation_evaluate,
    predict_short_conviction,
    temporal_splits,
    train_short_calibrated_model,
    train_short_horizon_model,
    walk_forward_evaluate,
)


def _short_frame(n: int = 60, seed: int = 3) -> pd.DataFrame:
    """Deterministic frame with both classes and a monotone as_of timestamp."""
    import numpy as np

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n)
    # Alternating-but-shuffled labels so each chunk is balanced.
    pos = (np.arange(n) % 2 == 0).astype(int)
    rng.shuffle(pos)
    rows = [
        {
            f: float(rng.normal(1.0 if p else -1.0)) for f in SHORT_FEATURE_COLUMNS
        }
        | {"as_of": d, "label_1_5d": int(p), "label_5_20d": int(p), "ticker": "AAA"}
        for d, p in zip(dates, pos, strict=True)
    ]
    return pd.DataFrame(rows)


def test_train_short_horizon_model_and_calibrated() -> None:
    frame = _short_frame()
    base = train_short_horizon_model(frame)
    assert list(base.get_params().items())
    assert base.n_features_in_ == len(SHORT_FEATURE_COLUMNS)

    calibrated = train_short_calibrated_model(frame)
    assert isinstance(calibrated, CalibratedModel)


def test_short_conviction_is_bounded() -> None:
    frame = _short_frame()
    model = train_short_calibrated_model(frame)
    row = {f: 0.0 for f in SHORT_FEATURE_COLUMNS}
    assert 0 <= predict_short_conviction(model, row) <= 100


def test_temporal_splits_are_strictly_time_ordered() -> None:
    frame = _short_frame(n=120, seed=5)
    folds = temporal_splits(frame, label_column="label_1_5d", n_splits=4)
    assert len(folds) >= 1
    for train, test in folds:
        train_max = pd.to_datetime(train["as_of"]).max()
        # Every test date must occur strictly after every train date (no leakage).
        assert (pd.to_datetime(test["as_of"]) > train_max).all()


def test_walk_forward_evaluate_returns_metrics() -> None:
    frame = _short_frame(n=240, seed=9)
    result = walk_forward_evaluate(frame, label_column="label_1_5d", n_splits=4)
    assert result.folds >= 1
    assert result.rows > 0
    assert 0.0 <= result.positive_rate <= 1.0


def test_ablation_evaluate_has_both_keys() -> None:
    frame = _short_frame(n=240, seed=11)
    result = ablation_evaluate(frame, label_column="label_1_5d", n_splits=4)
    assert set(result) == {"market_only", "market_and_events"}


def test_labels_and_feature_columns_are_exported() -> None:
    assert SHORT_HORIZON_LABELS == ("label_1_5d", "label_5_20d")
    assert len(SHORT_FEATURE_COLUMNS) == len(  # market + event, no overlap by name
        set(SHORT_FEATURE_COLUMNS)
    )
    tail = SHORT_FEATURE_COLUMNS[-len(EVENT_FEATURE_COLUMNS):]
    assert all(f in EVENT_FEATURE_COLUMNS for f in tail)