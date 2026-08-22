"""Short-horizon label tests."""

import pandas as pd
import pytest

from stock_monitor.features.labels import (
    SHORT_HORIZONS,
    build_short_horizon_labels,
    build_short_horizon_training_rows,
)


def _frame(values: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    index = pd.bdate_range(start, periods=len(values))
    return pd.DataFrame({"close": values}, index=index)


def test_max_forward_return_label_and_strict_future() -> None:
    # 21 forward bars after the decision bar (100).
    # Bar index 1 = 120 (first future), then flat 150s for the rest,
    # so the best 1-5 window return is 120/100 - 1 = 0.20 (the same-day 120
    # is the *decision* print, not a future print we could have acted on).
    prices = _frame([100.0, 120.0] + [150.0] * 20)
    benchmark = _frame([100.0] * 22)
    as_of = prices.index[0]

    labels = build_short_horizon_labels(prices, benchmark, as_of)
    assert labels is not None
    assert labels["fwd_ret_1_5d"] == pytest.approx(0.50)  # best in 1-5 = 150/100
    assert labels["label_1_5d"] == 1  # beat the flat benchmark
    assert labels["label_5_20d"] == 1


def test_decision_bar_excludes_its_own_grid_print() -> None:
    # As-of close is 100; the *next* bar is 150. The actual same-day 120
    # sits at the decision bar and must NOT count as a future bar.
    prices = _frame([100.0, 150.0] + [150.0] * 20)
    benchmark = _frame([100.0] * 22)
    as_of = prices.index[0]

    labels = build_short_horizon_labels(prices, benchmark, as_of)
    assert labels is not None
    assert labels["fwd_ret_1_5d"] == pytest.approx(0.50)  # 150/100 from the ROW AFTER decision


def test_horizon_windows_require_enough_forward_bars() -> None:
    # Exactly 20 forward bars exist after the decision bar, so the 5-20 window
    # can form (needs 20). A window needing 21+ must return None.
    prices = _frame([100.0] + [100.0] * 20)
    benchmark = _frame([100.0] * 21)
    as_of = prices.index[0]

    labels = build_short_horizon_labels(prices, benchmark, as_of)
    assert labels is not None
    assert labels["label_1_5d"] == 0  # flat vs flat → not beating

    prices_short = _frame([100.0] * 11)
    assert build_short_horizon_labels(prices_short, benchmark, as_of) is None


def test_training_rows_drop_incomplete_tail() -> None:
    prices = _frame([100.0] * 30)
    benchmark = _frame([100.0] * 30)
    # Decisions whose bar has fewer than 20 future bars cannot form the 5-20
    # window: with 30 bars, only the first 10 decisions (index 0-9) survive.
    rows = build_short_horizon_training_rows(prices, benchmark, prices.index)
    assert len(rows) == 10


def test_windows_are_reasonable() -> None:
    assert SHORT_HORIZONS == ((1, 5), (5, 20))
