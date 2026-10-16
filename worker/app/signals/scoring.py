"""Scoring (DESIGN.md section 8). AI score and rules adjustment are stored separately."""

from dataclasses import dataclass

from app.ai.schemas import AnalystOutput
from app.rules.engine import RuleHit, adjustment_and_alignment
from app.signals.candidates import Candidate
from app.yaml_config import load_yaml


@dataclass(frozen=True)
class Score:
    components: dict[str, float]
    ai_total: float
    rules_adjustment: float
    priced_in_penalty: float
    final: float


def source_quality(c: Candidate) -> float:
    """Official filing = 100; otherwise grows with the number of distinct sources."""
    return 100.0 if c.has_official else min(90.0, 20.0 + 20.0 * len(c.sources))


def priced_in_penalty(premove_fraction: float | None) -> float:
    """Up to max_penalty points, scaled by how much of the predicted move already happened."""
    cfg = load_yaml("entry.yaml")["priced_in"]
    if not cfg["enabled"] or premove_fraction is None:
        return 0.0
    return round(float(cfg["max_penalty"]) * min(max(premove_fraction, 0.0), 1.0), 2)


def compute_score(
    out: AnalystOutput,
    cand: Candidate,
    hits: list[RuleHit],
    liquidity: float | None = None,
    premove_fraction: float | None = None,
) -> Score:
    """liquidity = live option liquidity score (placeholder when unpriced); premove_fraction =
    share of the predicted move already done before the signal (priced-in penalty)."""
    cfg = load_yaml("signals.yaml")
    adj, alignment = adjustment_and_alignment(
        hits, out.direction, float(cfg["rules_adjustment_cap"])
    )
    comps = {
        "impact": float(out.impact),
        "surprise": float(out.surprise),
        "clarity": float(out.clarity),
        "timing": float(out.timing),
        "source_quality": source_quality(cand),
        "liquidity": liquidity if liquidity is not None else float(cfg["liquidity_placeholder"]),
        "macro_alignment": alignment,
    }
    ai_total = sum(float(cfg["weights"][k]) * v for k, v in comps.items())
    penalty = priced_in_penalty(premove_fraction)
    final = max(0.0, min(100.0, ai_total + adj - penalty))
    return Score(comps, round(ai_total, 2), round(adj, 2), penalty, round(final, 2))
