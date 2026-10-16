"""Option-implied move vs our predicted move (DESIGN_V2 section 2)."""

import math

from app.market.options import OptionChain


def straddle_move_pct(chain: OptionChain) -> float | None:
    """Implied move to expiry ~ ATM straddle / spot, in %."""
    k = chain.atm_strike()
    ce, pe = chain.get(k, "CE"), chain.get(k, "PE")
    if not (ce and pe and ce.mid and pe.mid and chain.spot):
        return None
    return (ce.mid + pe.mid) / chain.spot * 100


def n_day_move_pct(atm_iv_pct: float, days: float) -> float:
    """Implied 1-sd move over `days` calendar days ~ IV x sqrt(days/365), in %."""
    return atm_iv_pct * math.sqrt(max(days, 0.0) / 365)


def event_iv_premium(iv_event_expiry: float | None, iv_next_expiry: float | None) -> float | None:
    """Extra IV (vol points) in the expiry that contains the event vs the next one."""
    if iv_event_expiry is None or iv_next_expiry is None:
        return None
    return iv_event_expiry - iv_next_expiry


def edge_ratio(
    predicted_move_in_direction: float | None, implied_move: float | None
) -> float | None:
    """>1 means we expect more than the market prices in. Negative prediction -> negative ratio."""
    if predicted_move_in_direction is None or not implied_move:
        return None
    return predicted_move_in_direction / implied_move
