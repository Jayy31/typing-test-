"""Modelled bid/ask, stale prices and R2 fill checks on end-of-day option data.

Bhavcopy prices are trades (roughly mid). Until the spread model is fitted from our live quote
archive: ask = price + half-spread, bid = price - half-spread, half-spread by tier (moneyness,
liquidity, large cap) from config/backtest.yaml, never below one tick, times a sensitivity
multiple. A zero-volume close is not a tradable price: the settlement price is used instead
(flagged); with neither there is no entry or exit that day.
"""

from dataclasses import dataclass
from typing import Any

from app.backtest.data import OptRow
from app.yaml_config import load_yaml


def _cfg() -> dict[str, Any]:
    return dict(load_yaml("backtest.yaml"))


@dataclass(frozen=True)
class Tier:
    name: str
    half_spread_pct: float


def tier_for(
    moneyness_pct: float, oi_lots: float | None, volume_lots: float | None, large_cap: bool
) -> Tier:
    for t in _cfg()["spread"]["tiers"]:
        if t.get("large_cap") and not large_cap:
            continue
        if "max_moneyness_pct" in t and moneyness_pct > float(t["max_moneyness_pct"]):
            continue
        if "min_oi_lots" in t and (oi_lots or 0) < float(t["min_oi_lots"]):
            continue
        if "min_volume_lots" in t and (volume_lots or 0) < float(t["min_volume_lots"]):
            continue
        return Tier(str(t["name"]), float(t["half_spread_pct"]))
    raise ValueError("backtest.yaml spread.tiers needs a catch-all last tier")


def half_spread(price: float, tier: Tier, mult: float) -> float:
    return max(
        price * tier.half_spread_pct / 100 * mult,
        float(_cfg()["spread"]["min_half_spread_inr"]) * mult,
    )


@dataclass(frozen=True)
class DayPrice:
    """Tradable reference price for a day: the close if it traded, else settlement (stale)."""

    open: float
    high: float
    low: float
    close: float
    stale: bool  # settlement price used (zero volume)


def tradable(row: OptRow | None) -> DayPrice | None:
    if row is None:
        return None
    if (row.volume or 0) > 0 and row.close and row.close > 0:
        o = row.open or row.close
        return DayPrice(
            o,
            max(row.high or row.close, o, row.close),
            min(row.low or row.close, o, row.close),
            row.close,
            False,
        )
    if _cfg()["stale_prices"]["use_settlement"] and row.settle and row.settle > 0:
        s = row.settle
        return DayPrice(s, s, s, s, True)
    return None


def bid(p: float, hs: float) -> float:
    return max(p - hs, 0.05)


def classify(lots: int, volume_lots: float | None, stale: bool) -> str:
    """R2 on daily volume: fillable / partial / unfillable (stale = settlement price used)."""
    if stale or not volume_lots:
        return "unfillable"
    share = float(_cfg()["fills"]["max_participation"])
    if lots <= share * volume_lots:
        return "fillable"
    return "partial" if lots <= volume_lots else "unfillable"
