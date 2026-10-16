"""Predicted move and where it came from (shown on every signal and in /why).

Sources, in the order they become available:
  scenario_tree - probability-weighted move from the analyst/judge scenario tree (now)
  analogues     - historical event-study stats (Phase 4)
  blended       - weighted blend of the above (+ probability model) once both exist (Phase 5)
"""

from dataclasses import dataclass

from app.ai.schemas import Scenario


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
