"""Sign and strength of a graph edge for an event: the default table (edge type x event type, in
config/kg.yaml) until the event study has ANALOGUE_MIN_N linked-stock reactions for that pair,
then the learned base rate. 'unknown' = support only."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app import taxonomy
from app.config import get_settings
from app.history import analogues
from app.yaml_config import load_yaml

SIGN = {"same": 1, "opposite": -1}


def kg_event_type(edge_type: str, event_type: str | None) -> str:
    """Event-study key for linked-stock reactions, e.g. 'kg:competitor_of:disaster'."""
    return f"kg:{edge_type}:{taxonomy.parent(event_type) or 'other'}"


def default_sign(edge_type: str, event_type: str | None) -> str:
    c = load_yaml("kg.yaml")
    parent = taxonomy.parent(event_type) or "other"
    return str((c["signs"].get(edge_type) or {}).get(parent, c["default_sign"]))


@dataclass(frozen=True)
class EdgeSign:
    sign: int  # +1 same, -1 opposite, 0 unknown (support only)
    strength: float  # 0..1 multiplier on the source sentiment
    basis: str  # default / base_rate / unknown
    detail: str


def resolve(
    edge_type: str,
    event_type: str | None,
    target: str,
    sector: str | None,
    today: date,
    lookup: Callable[..., Any] | None = None,
) -> EdgeSign:
    """Linked reactions are measured in the direction (source direction x default sign; 'same'
    when the default is unknown), so a positive mean confirms that baseline, a negative one flips
    it."""
    c = load_yaml("kg.yaml")["evidence"]
    d = default_sign(edge_type, event_type)
    base = SIGN.get(d, 1)
    key = kg_event_type(edge_type, event_type)
    res = (lookup or analogues.lookup)(
        [key], sector, target, "bullish", today, int(c["base_rate_window"])
    )
    if res is not None and res.usable:
        strength = math.tanh(abs(res.mean) / float(c["base_rate_scale_pct"]))
        sign = base * (1 if res.mean >= 0 else -1)
        word = "same" if sign > 0 else "opposite"
        return EdgeSign(
            sign,
            round(strength, 3),
            "base_rate",
            f"event-study base rate for {key}: {word} (n={res.n}, avg {res.mean:+.2f}%, "
            f"hit {res.hit_rate:.0%})",
        )
    n = res.n if res is not None else 0
    if d not in SIGN:
        return EdgeSign(0, 0.0, "unknown", f"sign unknown for {key} (support only; n={n})")
    return EdgeSign(
        base,
        float(c["rule_novelty"]),
        "default",
        f"default table: {d} for {key} (low weight; event study n={n} < "
        f"{get_settings().analogue_min_n})",
    )
