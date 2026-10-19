"""Regime-conditioned analogue stats (feat.regime): the stats of events in the current regime,
mixed with all-regime stats at n_eff / (n_eff + prior_n). Below ANALOGUE_MIN_N regime events the
regime part gets no weight (the all-regime stats stand). Shared by live lookup and the backtester.
"""

from dataclasses import replace
from typing import Any

from app.config import get_settings
from app.regime.classifier import cfg

PREFIX = "R:"  # analogue_stats regime key for the Phase 12 labels (old VIX keys stay as they are)


def key(label: str | None) -> str | None:
    return f"{PREFIX}{label}" if label and "unknown" not in label else None


def prior_n() -> float:
    v = cfg()["analogues"].get("prior_n")
    return float(v if v is not None else get_settings().analogue_prior_n)


def regime_weight(n: int, n_eff: float) -> float:
    if n < get_settings().analogue_min_n:
        return 0.0
    return n_eff / (n_eff + prior_n())


def blend(all_res: Any, reg_res: Any | None, label: str) -> Any:
    """AnalogueResult mixed toward the regime stats; `regime` says how much regime data was used."""
    w = regime_weight(reg_res.n, reg_res.n_eff) if reg_res is not None else 0.0
    if reg_res is None or w == 0.0:
        return replace(all_res, regime=f"all (regime {label}: too few events)")

    def mix(a: float, b: float) -> float:
        return w * b + (1 - w) * a

    return replace(
        all_res,
        regime=f"{label} {w:.0%} + all {1 - w:.0%}",
        mean=mix(all_res.mean, reg_res.mean),
        median=mix(all_res.median, reg_res.median),
        hit_rate=mix(all_res.hit_rate, reg_res.hit_rate),
        std=mix(all_res.std, reg_res.std),
        p25=mix(all_res.p25, reg_res.p25),
        p75=mix(all_res.p75, reg_res.p75),
    )
