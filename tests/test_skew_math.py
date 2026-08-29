"""Unit tests for pure math functions in skew_math."""

from __future__ import annotations

import math

from stock_monitor.skew_math import (
    bs_price,
    classify_quadrant,
    compute_skew_metrics,
    delta_from_iv,
    find_25d_legs,
    find_atm_iv,
    interpolate_target_delta_iv,
    normal_cdf,
)


def test_normal_cdf() -> None:
    assert math.isclose(normal_cdf(0.0), 0.5, abs_tol=1e-6)
    assert math.isclose(normal_cdf(1.96), 0.975002, abs_tol=1e-4)
    assert math.isclose(normal_cdf(-1.96), 0.024998, abs_tol=1e-4)


def test_bs_price_and_delta() -> None:
    spot = 100.0
    strike = 100.0
    t_years = 30.0 / 365.0
    iv = 0.25
    r = 0.05
    q = 0.0

    call_price = bs_price(spot, strike, t_years, r, q, iv, is_call=True)
    put_price = bs_price(spot, strike, t_years, r, q, iv, is_call=False)

    assert call_price > 0
    assert put_price > 0
    # Put-Call Parity: C - P = S*exp(-q*T) - K*exp(-r*T)
    expected_diff = spot * math.exp(-q * t_years) - strike * math.exp(-r * t_years)
    assert math.isclose(call_price - put_price, expected_diff, abs_tol=1e-4)

    c_delta = delta_from_iv(spot, strike, t_years, r, q, iv, is_call=True)
    p_delta = delta_from_iv(spot, strike, t_years, r, q, iv, is_call=False)

    assert 0.45 < c_delta < 0.60
    assert -0.55 < p_delta < -0.40
    # Delta relation: call_delta - put_delta = exp(-q*T) = 1.0 when q=0
    assert math.isclose(c_delta - p_delta, 1.0, abs_tol=1e-5)


def test_find_atm_iv() -> None:
    strikes = [90.0, 95.0, 100.0, 105.0, 110.0]
    call_ivs = {90.0: 0.30, 95.0: 0.28, 100.0: 0.25, 105.0: 0.24, 110.0: 0.23}
    put_ivs = {90.0: 0.31, 95.0: 0.29, 100.0: 0.26, 105.0: 0.24, 110.0: 0.22}

    atm_iv = find_atm_iv(strikes=strikes, call_ivs=call_ivs, put_ivs=put_ivs, spot=101.0)
    # Strike 100 is closest to 101.0 -> average of 0.25 and 0.26 = 0.255
    assert atm_iv is not None
    assert math.isclose(atm_iv, 0.255, abs_tol=1e-4)


def test_interpolate_target_delta_iv() -> None:
    # Synthetic pairs: strike 110 (delta 0.35, IV 0.26), strike 120 (delta 0.15, IV 0.28)
    pairs = [
        (100.0, 0.50, 0.25),
        (110.0, 0.35, 0.26),
        (120.0, 0.15, 0.28),
        (130.0, 0.05, 0.30),
    ]
    res = interpolate_target_delta_iv(pairs, target_abs_delta=0.25)
    assert res is not None
    target_k, target_iv = res
    # 0.25 is halfway between 0.35 and 0.15 -> expected IV is 0.27
    assert math.isclose(target_iv, 0.27, abs_tol=1e-4)


def test_find_25d_legs() -> None:
    spot = 100.0
    t_years = 30.0 / 365.0
    strikes = [80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0]
    call_ivs = {s: 0.25 for s in strikes}
    put_ivs = {s: 0.25 for s in strikes}

    c_leg, p_leg = find_25d_legs(strikes, call_ivs, put_ivs, spot, t_years)
    assert c_leg is not None
    assert p_leg is not None
    c_strike, c_iv = c_leg
    p_strike, p_iv = p_leg
    assert math.isclose(c_iv, 0.25, abs_tol=1e-2)
    assert math.isclose(p_iv, 0.25, abs_tol=1e-2)


def test_classify_quadrant() -> None:
    # 1. Contrarian Bid: Down 1M (ret < 0) + Calls Bid (skew < 0)
    assert classify_quadrant(ret_1m=-0.05, normalized_skew=-0.12) == "Contrarian Bid"

    # 2. Chase: Up 1M (ret >= 0) + Calls Bid (skew < 0)
    assert classify_quadrant(ret_1m=0.08, normalized_skew=-0.15) == "Chase"

    # 3. Hedged Rally: Up 1M (ret >= 0) + Puts Bid (skew >= 0)
    assert classify_quadrant(ret_1m=0.06, normalized_skew=0.18) == "Hedged Rally"

    # 4. Fear: Down 1M (ret < 0) + Puts Bid (skew >= 0)
    assert classify_quadrant(ret_1m=-0.04, normalized_skew=0.22) == "Fear"


def test_compute_skew_metrics_sanity_rejections() -> None:
    strikes = [80.0, 90.0, 100.0, 110.0, 120.0]
    call_ivs = {s: 0.25 for s in strikes}
    put_ivs = {s: 0.25 for s in strikes}

    # Spot <= 0
    assert compute_skew_metrics(0.0, strikes, call_ivs, put_ivs, 30, 0.0) is None

    # DTE <= 0
    assert compute_skew_metrics(100.0, strikes, call_ivs, put_ivs, 0, 0.0) is None

    # Insufficient strikes
    assert compute_skew_metrics(100.0, [100.0], {100.0: 0.25}, {100.0: 0.25}, 30, 0.0) is None


def test_compute_skew_metrics_full() -> None:
    spot = 100.0
    strikes = [80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0]
    # Put IVs higher than call IVs (typical structural equity skew)
    call_ivs = {s: 0.22 for s in strikes}
    put_ivs = {s: 0.28 for s in strikes}

    metrics = compute_skew_metrics(
        spot=spot,
        strikes=strikes,
        call_ivs=call_ivs,
        put_ivs=put_ivs,
        dte_days=30,
        ret_1m=-0.03,
    )
    assert metrics is not None
    assert metrics.spot == 100.0
    assert metrics.raw_skew > 0  # put_iv > call_iv
    assert metrics.normalized_skew > 0
    assert metrics.quadrant == "Fear"  # down 1M + puts bid
    assert metrics.sanity_passed is True
