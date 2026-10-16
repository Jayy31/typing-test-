import math

import numpy as np
import pytest

from app.pricing.bs import greeks, implied_vol, norm_cdf, price


def test_norm_cdf_accuracy() -> None:
    for x in (-3, -1.5, -0.2, 0, 0.7, 2.5):
        exact = 0.5 * (1 + math.erf(x / math.sqrt(2)))
        assert abs(float(norm_cdf(x)) - exact) < 2e-7


def test_known_black_scholes_value() -> None:
    # Hull's textbook example: S=42, K=40, r=10%, sigma=20%, T=0.5 -> call 4.76, put 0.81
    assert float(price(42, 40, 0.5, 0.10, 0.20, "CE")) == pytest.approx(4.76, abs=0.01)
    assert float(price(42, 40, 0.5, 0.10, 0.20, "PE")) == pytest.approx(0.81, abs=0.01)


def test_put_call_parity_and_vectorised() -> None:
    s = np.array([90.0, 100.0, 110.0])
    c, p = price(s, 100, 0.25, 0.065, 0.3, "CE"), price(s, 100, 0.25, 0.065, 0.3, "PE")
    assert np.allclose(c - p, s - 100 * math.exp(-0.065 * 0.25), atol=1e-6)


def test_expiry_gives_intrinsic() -> None:
    assert float(price(105, 100, 0.0, 0.065, 0.3, "CE")) == 5.0
    assert float(price(105, 100, 0.0, 0.065, 0.3, "PE")) == 0.0


def test_greeks_signs_and_finite_differences() -> None:
    g = greeks(100, 100, 30 / 365, 0.065, 0.3, "CE")
    assert 0.5 < g.delta < 0.6 and g.gamma > 0 and g.theta_per_day < 0 and g.vega_per_point > 0
    bump = float(price(100.01, 100, 30 / 365, 0.065, 0.3, "CE")) - float(
        price(99.99, 100, 30 / 365, 0.065, 0.3, "CE")
    )
    assert g.delta == pytest.approx(bump / 0.02, rel=1e-3)
    vbump = float(price(100, 100, 30 / 365, 0.065, 0.31, "CE")) - float(
        price(100, 100, 30 / 365, 0.065, 0.30, "CE")
    )
    assert g.vega_per_point == pytest.approx(vbump, rel=1e-2)
    assert greeks(100, 100, 30 / 365, 0.065, 0.3, "PE").delta < 0


def test_implied_vol_round_trip() -> None:
    for opt in ("CE", "PE"):
        prem = float(price(2000, 2050, 20 / 365, 0.065, 0.27, opt))
        iv = implied_vol(prem, 2000, 2050, 20 / 365, 0.065, opt)
        assert iv is not None and iv == pytest.approx(0.27, abs=1e-4)
    assert implied_vol(0.0, 100, 100, 0.1, 0.065, "CE") is None
    assert implied_vol(500.0, 100, 100, 0.1, 0.065, "CE") is None  # above no-arb bound
