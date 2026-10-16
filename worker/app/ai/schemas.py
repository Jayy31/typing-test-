"""Strict schemas for AI outputs (validated with Pydantic)."""

from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "results",
    "order_win",
    "mna",
    "regulatory",
    "policy",
    "macro_data",
    "geopolitical",
    "commodity",
    "management",
    "rating",
    "corporate_action",
    "block_deal",
    "legal_fraud",
    "disaster",
    "other",
]


class Impact(BaseModel):
    """One affected stock or sector. Exactly one of symbol / sector is set."""

    symbol: str | None = Field(description="NSE F&O symbol, or null for a sector-level impact")
    sector: str | None = Field(description="Sector name when no single stock is affected")
    direction: Literal["bullish", "bearish", "neutral"]
    sentiment: float = Field(ge=-1, le=1, description="-1 very negative to +1 very positive")


class TaggerOutput(BaseModel):
    impacts: list[Impact] = Field(description="Affected stocks/sectors; empty if none")
    themes: list[str] = Field(description="Macro themes such as crude, rates, tariffs")
    event_type: EventType
    novelty: float = Field(ge=0, le=1, description="0 = old/recap, 1 = brand-new fact")
    is_actionable: bool = Field(description="True only if this could move an F&O stock or sector")
    reason: str = Field(description="One sentence justification")
