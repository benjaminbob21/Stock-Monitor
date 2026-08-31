"""Calibration + calibrated-scorer tests (network-free)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from stock_monitor.features.builder import build_feature_row
from stock_monitor.models.calibration import CalibratedModel, fit_calibrator
from stock_monitor.models.scorer import score_row, train_calibrated_model


def test_fit_calibrator_is_monotonic_and_bounded() -> None:
    rng = np.random.default_rng(0)
    raw = rng.uniform(0, 1, 200)
    y = (raw > 0.5).astype(int)  # perfectly ordered -> calibrator should track it
    cal = fit_calibrator(raw, y, method="sigmoid")
    out = cal.transform([0.1, 0.5, 0.9])
    assert np.all((out >= 0.0) & (out <= 1.0))
    assert out[0] <= out[1] <= out[2]


def test_train_calibrated_model_produces_calibrated_scores(world: SimpleNamespace) -> None:
    model = train_calibrated_model(world.frame, method="sigmoid", cv=3)
    assert isinstance(model, CalibratedModel)
    assert model.calibrator is not None  # world.frame has enough data + both classes

    as_of = world.prices.index[-1].date()
    row = build_feature_row(world.ticker, world.prices, world.facts, as_of)
    assert row is not None

    result = score_row(model, row)
    assert result.calibrated is True
    assert 0 <= result.conviction <= 100
    assert 1 <= len(result.drivers) <= 3


def test_plain_model_scores_uncalibrated(world: SimpleNamespace) -> None:
    as_of = world.prices.index[-1].date()
    row = build_feature_row(world.ticker, world.prices, world.facts, as_of)
    assert row is not None
    result = score_row(world.model, row)  # plain LGBMClassifier
    assert result.calibrated is False
