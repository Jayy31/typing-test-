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


def compute_score(out: AnalystOutput, cand: Candidate, hits: list[RuleHit]) -> Score:
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
        "liquidity": float(cfg["liquidity_placeholder"]),
        "macro_alignment": alignment,
    }
    ai_total = sum(float(cfg["weights"][k]) * v for k, v in comps.items())
    penalty = 0.0  # priced-in penalty needs live prices (Phase 3)
    final = max(0.0, min(100.0, ai_total + adj - penalty))
    return Score(comps, round(ai_total, 2), round(adj, 2), penalty, round(final, 2))
