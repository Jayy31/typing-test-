"""Budget-aware Anthropic wrapper. Every call is cost-logged to ai_usage and capped.

Two modes: `parse` (one live structured call) and `submit_batch` / `batch_results`
(Message Batches API, half price, for one-off historical jobs; always estimate first).
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeVar, cast

import anthropic
from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine, func, select

from app.config import IST, Settings, get_settings
from app.db.models import AiUsage
from app.db.session import session_scope
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
ROLES = ("tagger", "researcher", "novelty", "analyst", "bull", "bear", "judge")


class BudgetExceededError(RuntimeError):
    """Daily or monthly AI budget reached; callers should skip or queue the work."""


class AiOutputError(RuntimeError):
    """The model refused or returned output that does not match the schema."""


class MessagesApi(Protocol):
    def parse(self, **kwargs: Any) -> Any: ...


class BatchesApi(Protocol):
    def create(self, **kwargs: Any) -> Any: ...
    def retrieve(self, batch_id: str) -> Any: ...
    def results(self, batch_id: str) -> Any: ...


def compute_cost_inr(
    role: str, input_t: int, output_t: int, cache_read: int, cache_write: int, batch: bool = False
) -> float:
    """Cost of one call in INR, using the role's price tier in config/ai.yaml.

    `input_t` is the uncached input; cache reads/writes are billed at their own rates.
    """
    cfg = load_yaml("ai.yaml")
    p = cfg["prices"][cfg["roles"][role]]
    usd = (
        input_t * p["input"]
        + output_t * p["output"]
        + cache_read * p["cache_read"]
        + cache_write * p["cache_write"]
    ) / 1_000_000
    if batch:
        usd *= float(cfg["batch_discount"])
    return float(usd * float(cfg["usd_inr"]))


@dataclass(frozen=True)
class CostEstimate:
    role: str
    items: int
    input_tokens: int
    output_tokens: int
    inr: float
    batch: bool

    def summary(self) -> str:
        mode = "Batch API (50% off)" if self.batch else "live calls"
        return (
            f"{self.items:,} items via {mode} on the '{self.role}' model: about "
            f"{self.input_tokens:,} input + {self.output_tokens:,} output tokens "
            f"= ~Rs {self.inr:,.0f}"
        )


def estimate_cost(
    role: str, items: int, in_per_item: int, out_per_item: int, batch: bool = True
) -> CostEstimate:
    """Estimate for a one-off job. Shown to the owner for approval BEFORE anything runs."""
    tin, tout = items * in_per_item, items * out_per_item
    return CostEstimate(
        role, items, tin, tout, compute_cost_inr(role, tin, tout, 0, 0, batch), batch
    )


class AiClient:
    def __init__(
        self,
        settings: Settings | None = None,
        engine: Engine | None = None,
        messages: MessagesApi | None = None,
        batches: BatchesApi | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.engine = engine
        self._msg_override = messages
        self._batch_override = batches
        self._sdk: anthropic.Anthropic | None = None

    def _client(self) -> anthropic.Anthropic:
        if self._sdk is None:
            self._sdk = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key or None, timeout=60.0
            )
        return self._sdk

    @property
    def _messages(self) -> MessagesApi:
        return self._msg_override or cast(MessagesApi, self._client().messages)

    @property
    def _batches(self) -> BatchesApi:
        return self._batch_override or cast(BatchesApi, self._client().messages.batches)

    def model_for(self, role: str) -> str:
        if role not in ROLES:
            raise ValueError(f"unknown AI role {role}")
        model = str(getattr(self.settings, f"ai_model_{role}"))
        if not model:
            raise RuntimeError(f"AI_MODEL_{role.upper()} is not set")
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

    @staticmethod
    def _system(system: str) -> list[dict[str, Any]]:
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def parse(
        self,
        role: str,
        system: str,
        user: str,
        output: type[T],
        max_tokens: int,
        thinking: dict[str, Any] | None = None,
    ) -> T:
        """One structured call. The system prompt is cached; usage is recorded."""
        self.check_budget()
        model = self.model_for(role)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": self._system(system),
            "messages": [{"role": "user", "content": user}],
            "output_format": output,
        }
        if thinking is not None:
            kwargs["thinking"] = thinking
        resp = self._messages.parse(**kwargs)
        self._record_usage(role, model, resp.usage)
        if resp.stop_reason == "refusal":
            raise AiOutputError("model refused the request")
        if resp.parsed_output is None:
            raise AiOutputError(f"no valid output (stop_reason={resp.stop_reason})")
        parsed: T = resp.parsed_output
        return parsed

    # ---- batch mode -------------------------------------------------------------------
    def submit_batch(
        self,
        role: str,
        system: str,
        items: list[tuple[str, str]],
        output: type[BaseModel],
        max_tokens: int,
        thinking: dict[str, Any] | None = None,
    ) -> str:
        """Submit (custom_id, user message) pairs as one Message Batch. Returns the batch id."""
        self.check_budget()
        model = self.model_for(role)
        schema = anthropic.transform_schema(output)
        requests = []
        for custom_id, user in items:
            params: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "system": self._system(system),
                "messages": [{"role": "user", "content": user}],
                "output_config": {"format": {"type": "json_schema", "schema": schema}},
            }
            if thinking is not None:
                params["thinking"] = thinking
            requests.append({"custom_id": custom_id, "params": params})
        return str(self._batches.create(requests=requests).id)

    def batch_done(self, batch_id: str) -> bool:
        return bool(self._batches.retrieve(batch_id).processing_status == "ended")

    def batch_results(self, role: str, batch_id: str, output: type[T]) -> dict[str, T | None]:
        """Validated outputs by custom_id (None = errored/invalid). Billed at batch price."""
        model = self.model_for(role)
        out: dict[str, T | None] = {}
        for r in self._batches.results(batch_id):
            if r.result.type != "succeeded":
                out[r.custom_id] = None
                continue
            msg = r.result.message
            self._record_usage(role, model, msg.usage, batch=True)
            text = next((b.text for b in msg.content if b.type == "text"), "")
            try:
                out[r.custom_id] = output.model_validate(json.loads(text))
            except (ValueError, ValidationError):
                out[r.custom_id] = None
        return out

    def _record_usage(self, role: str, model: str, usage: Any, batch: bool = False) -> None:
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = compute_cost_inr(
            role, usage.input_tokens, usage.output_tokens, cache_read, cache_write, batch
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
