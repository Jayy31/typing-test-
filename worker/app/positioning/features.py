"""Positioning features per stock per day (feat.positioning). Pure functions over one day's data,
shared by the history builder and the live path; thresholds in config/positioning.yaml.

States: oi_buildup (long_buildup / short_buildup / short_covering / long_unwinding / flat),
pcr (high / low / neutral, with _rising / _falling), iv_skew (put_skew / call_skew / flat),
term_structure (inverted / steep / flat), basis (premium / discount / flat), rollover (high / low /
normal, expiry week only), unusual_activity (unusual_calls / unusual_puts / none),
delivery (high / low / normal). A feature whose inputs are missing is left out.
"""

from dataclasses import dataclass, field
from typing import Any

from app.yaml_config import load_yaml


def cfg() -> dict[str, Any]:
    return load_yaml("positioning.yaml")


@dataclass(frozen=True)
class OptRow:
    strike: float
    type: str  # CE / PE
    oi: float | None = None
    chg_oi: float | None = None
    volume: float | None = None
    iv: float | None = None  # % (vol points)
    avg_volume: float | None = None  # 20-day averages for this strike (unusual activity)
    avg_abs_chg_oi: float | None = None


@dataclass(frozen=True)
class DayInput:
    spot: float | None = None
    prev_spot: float | None = None
    fut_close: float | None = None  # near-month future
    fut_prev_close: float | None = None
    fut_oi: float | None = None
    fut_chg_oi: float | None = None
    next_fut_oi: float | None = None
    days_to_expiry: int | None = None  # trading days to the near expiry
    near: list[OptRow] = field(default_factory=list)
    next_atm_iv: float | None = None
    prev_pcr: float | None = None
    delivery_pct: float | None = None
    delivery_avg: float | None = None


State = tuple[str, float]  # (state, value)


def oi_buildup(x: DayInput) -> State | None:
    if None in (x.fut_close, x.fut_prev_close, x.fut_oi, x.fut_chg_oi) or not x.fut_prev_close:
        return None
    c = cfg()["oi_buildup"]
    p = (x.fut_close / x.fut_prev_close - 1) * 100  # type: ignore[operator]
    prev_oi = (x.fut_oi or 0) - (x.fut_chg_oi or 0)
    if prev_oi <= 0:
        return None
    o = (x.fut_chg_oi or 0) / prev_oi * 100
    if abs(p) < float(c["min_price_chg_pct"]) or abs(o) < float(c["min_oi_chg_pct"]):
        return ("flat", round(p, 3))
    state = {
        (True, True): "long_buildup",
        (False, True): "short_buildup",
        (True, False): "short_covering",
        (False, False): "long_unwinding",
    }[(p > 0, o > 0)]
    return (state, round(p, 3))


def pcr_value(rows: list[OptRow]) -> float | None:
    calls = sum(r.oi or 0 for r in rows if r.type == "CE")
    puts = sum(r.oi or 0 for r in rows if r.type == "PE")
    return puts / calls if calls > 0 else None


def pcr(x: DayInput) -> State | None:
    v = pcr_value(x.near)
    if v is None:
        return None
    c = cfg()["pcr"]
    level = (
        "high" if v > float(c["high_above"]) else "low" if v < float(c["low_below"]) else "neutral"
    )
    if x.prev_pcr is not None:
        d = v - x.prev_pcr
        if d > float(c["change_min"]):
            level += "_rising"
        elif d < -float(c["change_min"]):
            level += "_falling"
    return (level, round(v, 3))


def _nearest(rows: list[OptRow], typ: str, target: float) -> OptRow | None:
    cands = [r for r in rows if r.type == typ and r.iv]
    return min(cands, key=lambda r: abs(r.strike - target)) if cands else None


def atm_iv(rows: list[OptRow], spot: float | None) -> float | None:
    if not spot:
        return None
    ivs = [r.iv for r in (_nearest(rows, "CE", spot), _nearest(rows, "PE", spot)) if r and r.iv]
    return sum(ivs) / len(ivs) if ivs else None


def iv_skew(x: DayInput) -> State | None:
    if not x.spot:
        return None
    c = cfg()["iv_skew"]
    off = float(c["otm_pct"]) / 100
    put, call = (
        _nearest(x.near, "PE", x.spot * (1 - off)),
        _nearest(x.near, "CE", x.spot * (1 + off)),
    )
    if not (put and call and put.iv and call.iv):
        return None
    v = put.iv - call.iv
    s = (
        "put_skew"
        if v > float(c["put_skew_above"])
        else "call_skew"
        if v < float(c["call_skew_below"])
        else "flat"
    )
    return (s, round(v, 3))


def term_structure(x: DayInput) -> State | None:
    near = atm_iv(x.near, x.spot)
    if near is None or x.next_atm_iv is None:
        return None
    c = cfg()["term_structure"]
    v = near - x.next_atm_iv
    s = (
        "inverted"
        if v > float(c["inverted_above"])
        else "steep"
        if v < float(c["steep_below"])
        else "flat"
    )
    return (s, round(v, 3))


def basis(x: DayInput) -> State | None:
    if not (x.fut_close and x.spot):
        return None
    c = cfg()["basis"]
    v = (x.fut_close / x.spot - 1) * 100
    s = (
        "premium"
        if v > float(c["premium_above"])
        else "discount"
        if v < float(c["discount_below"])
        else "flat"
    )
    return (s, round(v, 3))


def rollover(x: DayInput) -> State | None:
    c = cfg()["rollover"]
    if x.days_to_expiry is None or x.days_to_expiry > int(c["expiry_week_days"]):
        return None
    if not x.fut_oi or x.next_fut_oi is None:
        return None
    v = x.next_fut_oi / (x.fut_oi + x.next_fut_oi)
    s = "high" if v > float(c["high_above"]) else "low" if v < float(c["low_below"]) else "normal"
    return (s, round(v, 3))


def unusual_activity(x: DayInput) -> State | None:
    c = cfg()["unusual_activity"]
    if not any(r.avg_volume for r in x.near):
        return None
    hits = {"CE": 0.0, "PE": 0.0}
    for r in x.near:
        if not (r.volume and r.avg_volume and r.chg_oi is not None and r.avg_abs_chg_oi):
            continue
        if r.volume < float(c["min_volume"]):
            continue
        if (
            r.volume >= float(c["volume_mult"]) * r.avg_volume
            and r.chg_oi >= float(c["oi_mult"]) * r.avg_abs_chg_oi
        ):
            hits[r.type] = max(hits[r.type], r.volume / r.avg_volume)
    if not hits["CE"] and not hits["PE"]:
        return ("none", 0.0)
    if hits["CE"] >= hits["PE"]:
        return ("unusual_calls", round(hits["CE"], 2))
    return ("unusual_puts", round(hits["PE"], 2))


def delivery(x: DayInput) -> State | None:
    if x.delivery_pct is None or x.delivery_avg is None:
        return None
    c = cfg()["delivery"]
    v = x.delivery_pct - x.delivery_avg
    s = (
        "high"
        if v > float(c["high_above_pts"])
        else "low"
        if v < float(c["low_below_pts"])
        else "normal"
    )
    return (s, round(v, 3))


FEATURES = {
    "oi_buildup": oi_buildup,
    "pcr": pcr,
    "iv_skew": iv_skew,
    "term_structure": term_structure,
    "basis": basis,
    "rollover": rollover,
    "unusual_activity": unusual_activity,
    "delivery": delivery,
}


def compute(x: DayInput) -> dict[str, State]:
    out = {}
    for name, fn in FEATURES.items():
        st = fn(x)
        if st is not None:
            out[name] = st
    return out
