"""Predicted move and where it came from (shown on every signal and in /why).

Sources, in the order they become available:
  scenario_tree - probability-weighted move from the analyst/judge scenario tree (now)
  analogues     - historical event-study stats (Phase 4)
  blended       - weighted blend of the above (+ probability model) once both exist (Phase 5)
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ai.schemas import Scenario

if TYPE_CHECKING:
    from app.history.analogues import AnalogueResult


@dataclass(frozen=True)
class Prediction:
    move_pct: float  # expected stock move in the trade's direction (negative = against us)
    source: str  # scenario_tree / analogues / blended
    p_move: float | None = None  # P(move in our direction), uncalibrated until Phase 5


def scenario_expected_move(scenarios: list[Scenario]) -> float:
    """Probability-weighted signed stock move (%)."""
    return sum(s.probability * s.stock_move_pct for s in scenarios)


def from_scenarios(scenarios: list[Scenario], direction: str) -> Prediction | None:
    """Prediction in the trade direction; None for a neutral view (no trade direction)."""
    if direction not in ("bullish", "bearish") or not scenarios:
        return None
    sign = 1.0 if direction == "bullish" else -1.0
    move = sign * scenario_expected_move(scenarios)
    p_dir = sum(s.probability for s in scenarios if sign * s.stock_move_pct > 0)
    return Prediction(round(move, 3), "scenario_tree", round(p_dir, 4))


def shrink(pred: Prediction, ai_weight: float) -> Prediction:
    """Blend with the market's view (no expected move, 50/50 direction) until calibrated."""
    p = None if pred.p_move is None else round(ai_weight * pred.p_move + (1 - ai_weight) * 0.5, 4)
    return Prediction(round(pred.move_pct * ai_weight, 3), pred.source, p)


def analogue_scenarios(
    analogue: "AnalogueResult", direction: str, agree: bool, weight: float, window_days: int
) -> list[Scenario]:
    """The analogue distribution as three pseudo-scenarios (p25 / median / p75 quantiles), so the
    simulator can sample it alongside the AI tree. Stats are % moves in the news' direction;
    `agree` = our trade direction matches the news direction."""
    sign = (1.0 if direction == "bullish" else -1.0) * (1.0 if agree else -1.0)
    qs = ((0.3, analogue.p25), (0.4, analogue.median), (0.3, analogue.p75))
    return [
        Scenario(
            name=f"analogue {name}",
            probability=p * weight,
            stock_move_pct=max(-50.0, min(50.0, sign * q)),
            timing_days=max(1, window_days),
            triggers=[f"historical base rate (n={analogue.n})"],
            invalidation="n/a",
        )
        for (p, q), name in zip(qs, ("p25", "median", "p75"), strict=True)
    ]


def combine(
    ai_scenarios: list[Scenario],
    direction: str,
    analogue: "AnalogueResult | None",
    agree: bool,
) -> tuple[Prediction | None, list[Scenario]]:
    """Scenario tree alone, or blended with analogues weighted n / (n + ANALOGUE_PRIOR_N)
    (zero below ANALOGUE_MIN_N events)."""
    base = from_scenarios(ai_scenarios, direction)
    if base is None or analogue is None or analogue.weight <= 0:
        return base, ai_scenarios
    w = min(analogue.weight, 1.0)
    window = max(1, analogue.window)
    mixed = [s.model_copy(update={"probability": s.probability * (1 - w)}) for s in ai_scenarios]
    mixed += analogue_scenarios(analogue, direction, agree, w, window)
    blended = from_scenarios(mixed, direction)
    assert blended is not None
    return Prediction(blended.move_pct, "blended", blended.p_move), mixed
