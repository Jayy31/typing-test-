"""Probability calibration maths (pure, numpy): Platt scaling, isotonic regression, Brier score.

Every function takes per-record weights. Records from the same news event (cluster) share one
unit of weight (`cluster_weights`), so ten stocks reacting to one RBI decision count as one
independent outcome, not ten.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np

EPS = 1e-4


def cluster_weights(keys: list[str]) -> np.ndarray:
    counts = Counter(keys)
    return np.array([1.0 / counts[k] for k in keys])


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.asarray(np.log(p / (1 - p)))


def platt_fit(
    p: np.ndarray, y: np.ndarray, w: np.ndarray, l2: float = 1e-3, iters: int = 50
) -> tuple[float, float]:
    """Weighted logistic regression of the outcome on logit(p): calibrated = sigmoid(a*logit(p)+b).
    Small L2 penalty keeps it finite when the data are separable."""
    x = _logit(p)
    a, b = 1.0, 0.0
    for _ in range(iters):
        z = np.clip(a * x + b, -30, 30)
        q = 1 / (1 + np.exp(-z))
        r = w * (q - y)
        g = np.array([np.sum(r * x) + l2 * (a - 1), np.sum(r) + l2 * b])
        s = w * q * (1 - q)
        h = np.array([[np.sum(s * x * x) + l2, np.sum(s * x)], [np.sum(s * x), np.sum(s) + l2]])
        step = np.linalg.solve(h, g)
        a, b = a - step[0], b - step[1]
        if np.max(np.abs(step)) < 1e-9:
            break
    return float(a), float(b)


def platt_apply(p: np.ndarray | float, a: float, b: float) -> np.ndarray:
    z = np.clip(a * _logit(np.asarray(p, dtype=float)) + b, -30, 30)
    return np.asarray(1 / (1 + np.exp(-z)))


def isotonic_fit(p: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[list[float], list[float]]:
    """Weighted pool-adjacent-violators: a non-decreasing step map from raw p to outcome rate.
    Returned as (x, y) knots for linear interpolation."""
    order = np.argsort(p, kind="mergesort")
    xs, ys, ws = list(p[order]), list(y[order].astype(float)), list(w[order])
    blocks: list[list[float]] = []  # [weighted mean, weight, x_min, x_max]
    for x, v, wt in zip(xs, ys, ws, strict=True):
        blocks.append([v, wt, x, x])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            m2, w2, lo, _ = blocks.pop(-2)
            m1, w1, _, hi = blocks.pop()
            tw = w1 + w2
            blocks.append([(m1 * w1 + m2 * w2) / tw, tw, lo, hi])
    kx: list[float] = []
    ky: list[float] = []
    for m, _, lo, hi in blocks:
        kx += [lo, hi] if hi > lo else [lo]
        ky += [m, m] if hi > lo else [m]
    return [float(v) for v in kx], [float(np.clip(v, EPS, 1 - EPS)) for v in ky]


def isotonic_apply(p: np.ndarray | float, kx: list[float], ky: list[float]) -> np.ndarray:
    return np.asarray(np.interp(np.asarray(p, dtype=float), kx, ky))


def brier(p: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    return float(np.sum(w * (p - y) ** 2) / np.sum(w))


@dataclass(frozen=True)
class Fitted:
    method: str  # platt / isotonic
    params: dict[str, Any]
    n_records: int
    n_events: float  # independent events = sum of cluster weights
    brier_raw: float
    brier: float
    reliability: list[dict[str, float]] = field(default_factory=list)

    def apply(self, p: float) -> float:
        return float(apply_params(self.method, self.params, p))


def apply_params(method: str, params: dict[str, Any], p: float) -> float:
    if method == "platt":
        return float(platt_apply(p, params["a"], params["b"]))
    if method == "isotonic":
        return float(isotonic_apply(p, params["x"], params["y"]))
    raise ValueError(f"unknown calibration method {method}")


def reliability(
    p: np.ndarray, y: np.ndarray, w: np.ndarray, bins: int = 5
) -> list[dict[str, float]]:
    """Weighted predicted vs observed rate per probability bucket (for the digest and /why)."""
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.any():
            out.append(
                {
                    "lo": round(float(lo), 2),
                    "hi": round(float(hi), 2),
                    "predicted": round(float(np.average(p[m], weights=w[m])), 3),
                    "observed": round(float(np.average(y[m], weights=w[m])), 3),
                    "events": round(float(w[m].sum()), 1),
                }
            )
    return out


def fit(p: list[float], y: list[bool], keys: list[str], isotonic_min: float) -> Fitted:
    """Platt below `isotonic_min` independent events, isotonic from there (owner rule)."""
    pa, ya = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    w = cluster_weights(keys)
    n_events = float(w.sum())
    if n_events >= isotonic_min:
        kx, ky = isotonic_fit(pa, ya, w)
        method, params = "isotonic", dict[str, Any](x=kx, y=ky)
    else:
        a, b = platt_fit(pa, ya, w)
        method, params = "platt", dict[str, Any](a=a, b=b)
    cal = np.array([apply_params(method, params, v) for v in pa])
    return Fitted(
        method,
        params,
        len(pa),
        round(n_events, 2),
        brier(pa, ya, w),
        brier(cal, ya, w),
        reliability(cal, ya, w),
    )
