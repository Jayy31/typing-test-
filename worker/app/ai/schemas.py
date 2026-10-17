"""Strict schemas for AI outputs (validated with Pydantic)."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app import taxonomy


def _event_type_field() -> Any:
    """event_type is constrained to the shared taxonomy (config/taxonomy.yaml)."""
    return Field(
        description="Closest event type from the taxonomy",
        json_schema_extra={"enum": list[Any](taxonomy.event_types())},
    )


class Impact(BaseModel):
    """One affected stock or sector. Exactly one of symbol / sector is set."""

    symbol: str | None = Field(description="NSE F&O symbol, or null for a sector-level impact")
    sector: str | None = Field(description="Sector name when no single stock is affected")
    direction: Literal["bullish", "bearish", "neutral"]
    sentiment: float = Field(ge=-1, le=1, description="-1 very negative to +1 very positive")


class TaggerOutput(BaseModel):
    impacts: list[Impact] = Field(description="Affected stocks/sectors; empty if none")
    themes: list[str] = Field(description="Macro themes such as crude, rates, tariffs")
    event_type: str = _event_type_field()
    novelty: float = Field(ge=0, le=1, description="0 = old/recap, 1 = brand-new fact")
    is_actionable: bool = Field(description="True only if this could move an F&O stock or sector")
    reason: str = Field(description="One sentence justification")

    @field_validator("event_type")
    @classmethod
    def _known_event_type(cls, v: str) -> str:
        return v if v in taxonomy.event_types() else "other"


class Scenario(BaseModel):
    """One branch of the scenario tree. Moves are signed stock % moves (+ up, - down)."""

    name: str
    probability: float = Field(ge=0, le=1)
    stock_move_pct: float = Field(ge=-50, le=50, description="Signed stock move in %")
    timing_days: int = Field(ge=1, le=30, description="Trading days until the move plays out")
    triggers: list[str] = Field(description="What would make this scenario happen")
    invalidation: str = Field(description="What would rule this scenario out")


class AnalystOutput(BaseModel):
    """Stage 2 (strong model) deep analysis of one stock's news evidence."""

    direction: Literal["bullish", "bearish", "neutral"]
    thesis: str = Field(description="2-4 sentences: what happened and why the stock should move")
    expected_move_pct: float = Field(ge=0, le=50, description="Expected absolute stock move in %")
    window_days: int = Field(
        ge=1, le=30, description="Trading days over which the move should play out"
    )
    impact: int = Field(ge=0, le=100, description="Typical move size for this event on this stock")
    surprise: int = Field(ge=0, le=100, description="How unexpected versus consensus or prior news")
    clarity: int = Field(ge=0, le=100, description="Clear single direction vs mixed effects")
    timing: int = Field(ge=0, le=100, description="Move likely within days vs vague future")
    confidence: Literal["low", "medium", "high"]
    risks: list[str] = Field(description="What could make this trade fail")
    counter_arguments: list[str] = Field(description="Best arguments against the thesis")
    invalidators: list[str] = Field(description="Future facts that would break the thesis")
    cited_news_ids: list[int] = Field(
        description="IDs of the provided news items the thesis relies on"
    )
    analogue_notes: str | None = Field(
        description="How provided historical analogues compare; null if none were provided"
    )
    scenarios: list[Scenario] = Field(
        description="2-5 mutually exclusive scenarios whose probabilities sum to 1"
    )

    @model_validator(mode="after")
    def _probabilities_sum_to_one(self) -> "AnalystOutput":
        total = sum(s.probability for s in self.scenarios)
        if not 2 <= len(self.scenarios) <= 5 or abs(total - 1.0) > 0.02:
            raise ValueError(
                f"need 2-5 scenarios with probabilities summing to 1 (got {total:.2f})"
            )
        return self


def _check_scenarios(scenarios: list[Scenario]) -> None:
    total = sum(s.probability for s in scenarios)
    if not 2 <= len(scenarios) <= 5 or abs(total - 1.0) > 0.02:
        raise ValueError(f"need 2-5 scenarios with probabilities summing to 1 (got {total:.2f})")


# ---- multi-agent panel (Phase 5) ------------------------------------------------------
class Fact(BaseModel):
    fact: str
    confirmed: bool = Field(description="Official filing or several independent sources")
    news_ids: list[int] = Field(description="Ids of the provided news items stating this fact")


class ResearchOutput(BaseModel):
    """Researcher (cheap model): facts only, no opinion on direction."""

    facts: list[Fact]
    key_numbers: list[str] = Field(description="Numbers that matter (amounts, %, dates)")
    open_questions: list[str] = Field(description="Unknown or contradictory points")
    cited_news_ids: list[int]


class NoveltyOutput(BaseModel):
    """Novelty checker (cheap model): how new the information is and how much is priced in."""

    novelty: float = Field(ge=0, le=1, description="0 = old/recap, 1 = brand-new material fact")
    priced_in: Literal["not_priced", "partly", "fully"]
    reasons: list[str]
    cited_news_ids: list[int]


class Argument(BaseModel):
    point: str
    news_ids: list[int] = Field(description="Ids of the provided news items this rests on")


class CaseOutput(BaseModel):
    """Bull or bear advocate (mid model): the strongest honest case for one side."""

    side: Literal["bull", "bear"]
    thesis: str
    arguments: list[Argument]
    weaknesses: list[str] = Field(description="Weak points of this case")
    expected_move_pct: float = Field(ge=0, le=50, description="Absolute stock move in % if right")
    window_days: int = Field(ge=1, le=30)
    confidence: Literal["low", "medium", "high"]
    scenarios: list[Scenario] = Field(description="2-5 scenarios whose probabilities sum to 1")
    cited_news_ids: list[int]

    @model_validator(mode="after")
    def _scenarios_ok(self) -> "CaseOutput":
        _check_scenarios(self.scenarios)
        return self


class JudgeOutput(AnalystOutput):
    """Judge (strongest model): the final view. Same fields as the single analyst plus the
    ruling, so everything downstream treats panel and single predictions alike."""

    winning_side: Literal["bull", "bear", "neither"]
    rejected_side_reasons: list[str] = Field(description="Why the losing case (or both) failed")


# ---- post-mortems (B4) ----------------------------------------------------------------
PostMortemCause = Literal[
    "thesis_right",
    "thesis_wrong",
    "timing",
    "priced_in",
    "iv_crush",
    "theta_decay",
    "slippage_gap",
    "liquidity",
    "market_move",
    "news_reversal",
    "exit_rules",
    "lucky",
    "other",
]


class PostMortemOutput(BaseModel):
    primary_cause: PostMortemCause
    secondary_causes: list[PostMortemCause]
    thesis_correct: bool | None = Field(
        description="Did the stock move as the thesis said? null if unclear"
    )
    summary: str = Field(description="2-3 sentences")
    what_went_right: list[str]
    what_went_wrong: list[str]
    lessons: list[str]
