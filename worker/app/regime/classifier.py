"""Market regime classifier (feat.regime): deterministic, no AI, values in config/regime.yaml.

`classify` labels one day from India VIX (level + percentile), the Nifty 20-day return and the FII
10-day net flow. Inputs that are missing are skipped and the label says which were used, so a
history day (no FII series) reads e.g. "normal/risk_on (VIX+Nifty; FII unknown)".
"""

from bisect import bisect_left
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.yaml_config import load_yaml

VOLS = ("calm", "normal", "stressed")


def cfg() -> dict[str, Any]:
    return load_yaml("regime.yaml")


@dataclass(frozen=True)
class Regime:
    day: date
    vol: str  # calm / normal / stressed / unknown
    direction: str  # risk_on / risk_off / unknown
    vix: float | None = None
    vix_pct: float | None = None
    nifty_ret_pct: float | None = None
    fii_net_cr: float | None = None
    inputs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return f"{self.vol}/{self.direction}"

    @property
    def inputs_text(self) -> str:
        names = ["VIX", "Nifty", "FII"]
        used = "+".join(n for n in names if n in self.inputs) or "none"
        missing = [n for n in names if n not in self.inputs]
        return used + (f"; {', '.join(missing)} unknown" if missing else "")

    def text(self) -> str:
        bits = []
        if self.vix is not None:
            pct = f", {self.vix_pct:.0f}th pct" if self.vix_pct is not None else ""
            bits.append(f"VIX {self.vix:.1f}{pct}")
        if self.nifty_ret_pct is not None:
            bits.append(f"Nifty 20d {self.nifty_ret_pct:+.1f}%")
        if self.fii_net_cr is not None:
            bits.append(f"FII 10d Rs {self.fii_net_cr:+,.0f} cr")
        return f"{self.label} ({self.inputs_text}): " + ", ".join(bits)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(day=self.day.isoformat(), label=self.label, inputs=list(self.inputs))
        return d


def percentile(history: list[float], value: float) -> float:
    s = sorted(history)
    return 100.0 * bisect_left(s, value) / len(s)


def classify(
    day: date,
    vix: float | None,
    vix_history: list[float] | None = None,
    nifty_ret_pct: float | None = None,
    fii_net_cr: float | None = None,
) -> Regime:
    c = cfg()
    v, d = c["volatility"], c["direction"]
    inputs: list[str] = []
    vol, pct = "unknown", None
    if vix is not None:
        inputs.append("VIX")
        if vix_history and len(vix_history) >= int(v["pct_min_days"]):
            pct = percentile(vix_history, vix)
        if vix > float(v["stressed_vix_above"]) or (
            pct is not None and pct > float(v["stressed_pct_above"])
        ):
            vol = "stressed"
        elif vix < float(v["calm_vix_below"]) and (pct is None or pct < float(v["calm_pct_below"])):
            vol = "calm"
        else:
            vol = "normal"
    votes: list[int] = []
    if nifty_ret_pct is not None:
        inputs.append("Nifty")
        up = float(d["nifty_up_pct"])
        votes.append(1 if nifty_ret_pct > up else -1 if nifty_ret_pct < -up else 0)
    if fii_net_cr is not None:
        inputs.append("FII")
        up = float(d["fii_up_cr"])
        votes.append(1 if fii_net_cr > up else -1 if fii_net_cr < -up else 0)
    direction = "unknown" if not votes else "risk_off" if sum(votes) < 0 else "risk_on"
    return Regime(day, vol, direction, vix, pct, nifty_ret_pct, fii_net_cr, tuple(inputs))


def series(
    nifty: dict[date, float], vix: dict[date, float], fii: dict[date, float] | None = None
) -> dict[date, Regime]:
    """Regime for every day with a VIX or Nifty close, using only data up to that day."""
    c = cfg()
    look, win = int(c["volatility"]["pct_lookback_days"]), int(c["direction"]["nifty_window_days"])
    fwin = int(c["direction"]["fii_window_days"])
    days = sorted(set(nifty) | set(vix))
    n_days = sorted(nifty)
    n_idx = {d: i for i, d in enumerate(n_days)}
    v_days = sorted(vix)
    v_idx = {d: i for i, d in enumerate(v_days)}
    f_days = sorted(fii or {})
    out: dict[date, Regime] = {}
    for day in days:
        v = vix.get(day)
        hist = (
            [vix[x] for x in v_days[max(0, v_idx[day] - look) : v_idx[day]]]
            if v is not None
            else None
        )
        ret = None
        if day in n_idx and n_idx[day] >= win:
            ret = (nifty[day] / nifty[n_days[n_idx[day] - win]] - 1) * 100
        f = None
        if fii:
            recent = [x for x in f_days if x <= day][-fwin:]
            if len(recent) == fwin:
                f = sum(fii[x] for x in recent)
        out[day] = classify(day, v, hist, ret, f)
    return out
