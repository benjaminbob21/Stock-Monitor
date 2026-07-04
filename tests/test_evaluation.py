"""Walk-forward validation tests (network-free)."""

from __future__ import annotations

import pandas as pd

from stock_monitor.evaluation import evaluate_walk_forward, walk_forward_folds


def test_walk_forward_folds_are_time_ordered_and_purged() -> None:
    as_of = pd.Series(pd.date_range("2016-01-01", periods=60, freq="MS").date)
    folds = walk_forward_folds(as_of, n_splits=3, embargo_months=12)
    assert folds, "expected at least one fold"
    for train_idx, test_idx in folds:
        # No overlap, and every test index comes strictly after every train index.
        assert set(train_idx).isdisjoint(test_idx)
        assert max(train_idx) < min(test_idx)


def test_evaluate_walk_forward_reports_calibration_metrics(pooled_frame: pd.DataFrame) -> None:
    report = evaluate_walk_forward(pooled_frame, n_splits=3, embargo_months=12)

    assert report.n > 0
    assert len(report.folds) >= 1
    # Brier scores are valid probabilities-of-error in [0, 1].
    assert 0.0 <= report.brier_raw <= 1.0
    assert 0.0 <= report.brier_calibrated <= 1.0
    assert 0.0 <= report.accuracy <= 1.0
    assert 0.0 <= report.hit_rate <= 1.0
    if report.auc is not None:
        assert 0.0 <= report.auc <= 1.0
