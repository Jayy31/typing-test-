"""P&L attribution per trade (v2.1 B2): delta, theta, vega and residual, in Rs per position.

Between two observations the option's change is explained by Black-Scholes greeks taken at the
earlier observation: delta x dSpot (gamma lands in the residual), theta x days, vega x dIV
(vol points). Spread = bid/ask paid at entry. Residual = the rest (gamma, model error).
So delta + theta + vega + spread + residual = gross P&L before costs.
Only the part of the position still open in each interval counts.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.pricing.bs import greeks


@dataclass
class Obs:
    ts: str
    spot: float
    premium: float  # bid (what we could sell at)
    iv_pct: float
    t_years: float


@dataclass
class Attribution:
    delta: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    spread: float = 0.0  # paid at entry: bought at the ask, marked at the bid
    residual: float = 0.0
    last: dict[str, Any] | None = field(default=None)

    def add(self, obs: Obs, strike: float, option_type: str, r: float, units: float) -> None:
        """units = shares still open (lot_size x lots x remaining fraction) during the interval."""
        if self.last is not None:
            prev = Obs(**self.last)
            g = greeks(
                prev.spot,
                strike,
                max(prev.t_years, 1e-6),
                r,
                max(prev.iv_pct, 1.0) / 100,
                option_type,
            )
            days = max(
                (datetime.fromisoformat(obs.ts) - datetime.fromisoformat(prev.ts)).total_seconds()
                / 86400,
                0.0,
            )
            d = g.delta * (obs.spot - prev.spot) * units
            th = g.theta_per_day * days * units
            v = g.vega_per_point * (obs.iv_pct - prev.iv_pct) * units
            actual = (obs.premium - prev.premium) * units
            self.delta += d
            self.theta += th
            self.vega += v
            self.residual += actual - d - th - v
        self.last = obs.__dict__

    def to_json(self) -> dict[str, Any]:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}

    @classmethod
    def from_json(cls, d: dict[str, Any] | None) -> "Attribution":
        return cls(**d) if d else cls()
