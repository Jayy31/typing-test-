"""Budget-aware Anthropic wrapper. Every call is cost-logged to ai_usage and capped."""

import logging
from datetime import datetime
from typing import Any, Protocol, TypeVar, cast

import anthropic
from pydantic import BaseModel
from sqlalchemy import Engine, func, select

from app.config import IST, Settings, get_settings
from app.db.models import AiUsage
from app.db.session import session_scope
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
TIERS = ("tagger", "analyst", "reviewer")


class BudgetExceededError(RuntimeError):
    """Daily or monthly AI budget reached; callers should skip or queue the work."""


class AiOutputError(RuntimeError):
    """The model refused or returned output that does not match the schema."""


class MessagesApi(Protocol):
    def parse(self, **kwargs: Any) -> Any: ...


def compute_cost_inr(
    tier: str, input_t: int, output_t: int, cache_read: int, cache_write: int
) -> float:
    """Cost of one call in INR, using the per-tier USD prices in config/ai.yaml.

    `input_t` is the uncached input; cache reads/writes are billed at their own rates.
    """
    cfg = load_yaml("ai.yaml")
    p = cfg["tiers"][tier]
    usd = (
        input_t * p["input"]
        + output_t * p["output"]
        + cache_read * p["cache_read"]
        + cache_write * p["cache_write"]
    ) / 1_000_000
    return float(usd * float(cfg["usd_inr"]))


class AiClient:
    def __init__(
        self,
        settings: Settings | None = None,
        engine: Engine | None = None,
        messages: MessagesApi | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.engine = engine
        if messages is not None:
            self._messages = messages
        else:
            sdk = anthropic.Anthropic(api_key=self.settings.anthropic_api_key or None, timeout=60.0)
            self._messages = cast(MessagesApi, sdk.messages)

    def model_for(self, tier: str) -> str:
        model = {
            "tagger": self.settings.ai_model_tagger,
            "analyst": self.settings.ai_model_analyst,
            "reviewer": self.settings.ai_model_reviewer,
        }[tier]
        if not model:
            raise RuntimeError(f"AI_MODEL_{tier.upper()} is not set")
        return model

    def spend_inr(self, since: datetime) -> float:
        with session_scope(self.engine) as s:
            return float(
                s.scalar(
                    select(func.coalesce(func.sum(AiUsage.cost_inr), 0)).where(AiUsage.ts >= since)
                )
                or 0
            )

    def check_budget(self, now: datetime | None = None) -> None:
        now = now or datetime.now(IST)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        if self.spend_inr(day_start) >= self.settings.ai_daily_budget_inr:
            raise BudgetExceededError("daily AI budget reached")
        if self.spend_inr(month_start) >= self.settings.ai_monthly_budget_inr:
            raise BudgetExceededError("monthly AI budget reached")

    def parse(
        self,
        tier: str,
        system: str,
        user: str,
        output: type[T],
        max_tokens: int,
        thinking: dict[str, Any] | None = None,
    ) -> T:
        """One structured call. The system prompt is cached; usage is recorded."""
        self.check_budget()
        model = self.model_for(tier)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_format": output,
        }
        if thinking is not None:
            kwargs["thinking"] = thinking
        resp = self._messages.parse(**kwargs)
        self._record_usage(tier, model, resp.usage)
        if resp.stop_reason == "refusal":
            raise AiOutputError("model refused the request")
        if resp.parsed_output is None:
            raise AiOutputError(f"no valid output (stop_reason={resp.stop_reason})")
        parsed: T = resp.parsed_output
        return parsed

    def _record_usage(self, tier: str, model: str, usage: Any) -> None:
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = compute_cost_inr(
            tier, usage.input_tokens, usage.output_tokens, cache_read, cache_write
        )
        with session_scope(self.engine) as s:
            s.add(
                AiUsage(
                    ts=datetime.now(IST),
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cached_tokens=cache_read,
                    cost_inr=cost,
                )
            )
