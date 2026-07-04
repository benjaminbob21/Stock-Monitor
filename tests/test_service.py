"""Service-level tests: score_ticker + the near-term (3-month) secondary signal."""

from __future__ import annotations

from types import SimpleNamespace

from stock_monitor.features.builder import build_feature_row
from stock_monitor.models.scorer import predict_conviction
from stock_monitor.service import score_ticker


def test_predict_conviction_in_range(world: SimpleNamespace) -> None:
    row = build_feature_row(
        world.ticker, world.prices, world.facts, world.prices.index[-1].date()
    )
    assert row is not None
    conviction = predict_conviction(world.model, row)
    assert 0 <= conviction <= 100


def test_score_ticker_includes_short_horizon_when_model_present(
    world: SimpleNamespace,
) -> None:
    payload = score_ticker(
        "AAA",
        model=world.model,
        model_version=world.version,
        price_provider=world.price_provider,
        fundamental_provider=world.fundamental_provider,
        label_window_months=12,
        short_model=world.model,  # reuse as a stand-in short model
    )
    assert payload["conviction_3m"] is not None
    assert 0 <= payload["conviction_3m"] <= 100  # type: ignore[operator]
    assert payload["recommendation_3m"]


def test_score_ticker_omits_short_horizon_when_absent(world: SimpleNamespace) -> None:
    payload = score_ticker(
        "AAA",
        model=world.model,
        model_version=world.version,
        price_provider=world.price_provider,
        fundamental_provider=world.fundamental_provider,
        label_window_months=12,
        short_model=None,
    )
    assert payload["conviction_3m"] is None
    assert payload["recommendation_3m"] is None
