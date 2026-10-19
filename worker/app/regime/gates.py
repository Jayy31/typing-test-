"""Gate values for a market regime (feat.regime). Base values come from options.yaml and the
EDGE_RATIO_MIN setting; config/regime.yaml `gates.<vol>` overrides them (shipped empty, so every
regime uses the same gates). Backtest variants can supply their own override set."""

from typing import Any

from app.config import get_settings
from app.regime.classifier import cfg
from app.yaml_config import load_yaml

KEYS = ("edge_ratio_min", "min_p_t1_before_sl", "min_ev_pct_of_premium", "min_ev_pct")


def base() -> dict[str, float]:
    sel = load_yaml("options.yaml").get("selection") or {}
    return {
        "edge_ratio_min": float(get_settings().edge_ratio_min),
        "min_p_t1_before_sl": float(sel.get("min_p_t1_before_sl", 0.0)),
        "min_ev_pct_of_premium": float(sel.get("min_ev_pct_of_premium", 0.0)),
        "min_ev_pct": float(sel.get("min_ev_pct", 0.0)),
    }


def for_regime(vol: str | None, overrides: dict[str, Any] | None = None) -> dict[str, float]:
    """Gate values in `vol` (calm/normal/stressed). `overrides` = a backtest variant's set
    ({vol: {key: value}}); default = config regime.yaml `gates`."""
    out = base()
    table = overrides if overrides is not None else (cfg().get("gates") or {})
    for k, v in ((table or {}).get(vol or "") or {}).items():
        if k in KEYS:
            out[k] = float(v)
    return out
