"""Black-Scholes pricing and greeks (European; NSE stock options are European-style).

Vectorised with numpy so the Monte Carlo simulator can reprice thousands of paths at once.
Conventions: `t` in years, `sigma` as a decimal (0.30 = 30%), rates continuous, `q` dividend
yield. Greeks: theta per calendar day, vega per 1 volatility point (0.01).
"""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

F = NDArray[np.float64]
MIN_T = 1e-6


def norm_cdf(x: ArrayLike) -> F:
    """Standard normal CDF via the Abramowitz-Stegun 7.1.26 erf approximation (|err| < 1.5e-7)."""
    x = np.asarray(x, dtype=np.float64)
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (
        0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))
    )
    erf = 1.0 - poly * np.exp(-z * z)
    return np.asarray(0.5 * (1.0 + np.sign(x) * erf), dtype=np.float64)


def norm_pdf(x: ArrayLike) -> F:
    x = np.asarray(x, dtype=np.float64)
    return np.asarray(np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi), dtype=np.float64)


def _d1d2(
    s: ArrayLike, k: float, t: ArrayLike, r: float, sigma: ArrayLike, q: float
) -> tuple[F, F]:
    s_ = np.asarray(s, dtype=np.float64)
    t_ = np.maximum(np.asarray(t, dtype=np.float64), MIN_T)
    v = np.maximum(np.asarray(sigma, dtype=np.float64), 1e-4)
    d1 = (np.log(s_ / k) + (r - q + 0.5 * v * v) * t_) / (v * np.sqrt(t_))
    return d1, d1 - v * np.sqrt(t_)


def price(
    s: ArrayLike,
    k: float,
    t: ArrayLike,
    r: float,
    sigma: ArrayLike,
    option_type: str,
    q: float = 0.0,
) -> F:
    """Option price. At t <= 0 returns intrinsic value."""
    s_ = np.asarray(s, dtype=np.float64)
    t_ = np.asarray(t, dtype=np.float64)
    d1, d2 = _d1d2(s_, k, t_, r, sigma, q)
    tt = np.maximum(t_, MIN_T)
    if option_type == "CE":
        val = s_ * np.exp(-q * tt) * norm_cdf(d1) - k * np.exp(-r * tt) * norm_cdf(d2)
        intrinsic = np.maximum(s_ - k, 0.0)
    elif option_type == "PE":
        val = k * np.exp(-r * tt) * norm_cdf(-d2) - s_ * np.exp(-q * tt) * norm_cdf(-d1)
        intrinsic = np.maximum(k - s_, 0.0)
    else:
        raise ValueError(f"option_type must be CE or PE, got {option_type}")
    return np.asarray(np.where(t_ <= 0, intrinsic, np.maximum(val, 0.0)), dtype=np.float64)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta_per_day: float
    vega_per_point: float


def greeks(
    s: float, k: float, t: float, r: float, sigma: float, option_type: str, q: float = 0.0
) -> Greeks:
    t = max(t, MIN_T)
    d1, d2 = (float(x) for x in _d1d2(s, k, t, r, sigma, q))
    pdf = float(norm_pdf(d1))
    disc_q, disc_r = math.exp(-q * t), math.exp(-r * t)
    gamma = disc_q * pdf / (s * sigma * math.sqrt(t))
    vega = s * disc_q * pdf * math.sqrt(t) / 100
    common = -s * disc_q * pdf * sigma / (2 * math.sqrt(t))
    if option_type == "CE":
        delta = disc_q * float(norm_cdf(d1))
        theta = common - r * k * disc_r * float(norm_cdf(d2)) + q * s * disc_q * float(norm_cdf(d1))
    else:
        delta = disc_q * (float(norm_cdf(d1)) - 1)
        theta = (
            common + r * k * disc_r * float(norm_cdf(-d2)) - q * s * disc_q * float(norm_cdf(-d1))
        )
    return Greeks(delta, gamma, theta / 365, vega)


def implied_vol(
    premium: float, s: float, k: float, t: float, r: float, option_type: str, q: float = 0.0
) -> float | None:
    """IV by bisection (robust, no derivatives). None if the premium is outside no-arb bounds."""
    lo, hi = 1e-4, 5.0
    if premium <= 0 or t <= 0:
        return None
    p_lo = float(price(s, k, t, r, lo, option_type, q))
    p_hi = float(price(s, k, t, r, hi, option_type, q))
    if not p_lo <= premium <= p_hi:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if float(price(s, k, t, r, mid, option_type, q)) < premium:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2
