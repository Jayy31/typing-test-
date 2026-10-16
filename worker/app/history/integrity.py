"""AI look-ahead protection (DESIGN_V2 R1).

Models know market outcomes up to their training cutoff. Before AI_MODEL_KNOWLEDGE_CUTOFF:
AI may only extract labels (event type, entities, sentiment, surprise). Any AI *prediction*
(direction to trade, move size, probabilities, outcomes) on such dates is refused.
An empty cutoff means every historical date is treated as contaminated.
"""

from datetime import date

from app.config import get_settings

LABELS_ONLY = "labels_only"
PREDICTIONS = "predictions"


class LookAheadError(RuntimeError):
    """An AI prediction was requested for a date the model may already 'know' the outcome of."""


def knowledge_cutoff() -> date | None:
    raw = get_settings().ai_model_knowledge_cutoff
    return date.fromisoformat(raw) if raw else None


def ai_predictions_allowed(day: date) -> bool:
    cutoff = knowledge_cutoff()
    return cutoff is not None and day > cutoff


def require_post_cutoff(day: date, what: str) -> None:
    if not ai_predictions_allowed(day):
        cutoff = knowledge_cutoff()
        raise LookAheadError(
            f"{what} for {day} refused: on or before the AI knowledge cutoff "
            f"({cutoff or 'not set'}) the AI may only extract labels (DESIGN_V2 R1)"
        )
