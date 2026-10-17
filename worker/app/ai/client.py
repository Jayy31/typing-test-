"""Budget-aware Anthropic wrapper. Every call is cost-logged to ai_usage and capped.

Two modes: `parse` (one live structured call) and `submit_batch` / `batch_results`
(Message Batches API, half price, for one-off historical jobs; always estimate first).
"""

import json
import logging
import time
from dataclasses import dataclass, replace
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
ROLES = (
    "tagger",
    "researcher",
    "novelty",
    "analyst",
    "bull",
    "bear",
    "judge",
    "postmortem",
    "extractor",
    "extractor_fallback",
    "kg_extractor",
    "report_writer",
)
# roles that reuse another role's model id. Phase 7 extraction (results, auto sales): the cheap
# model first, the mid model as the fallback.
MODEL_ROLE = {
    "postmortem": "analyst",
    "extractor": "tagger",
    "extractor_fallback": "analyst",
    "kg_extractor": "tagger",  # Phase 8 company-link extraction (cheap)
    "report_writer": "tagger",  # Phase 10 monthly report summary paragraph (cheap)
}
RESERVED_ROLES = ("tagger",)  # may spend the AI_TAGGER_RESERVE_INR kept back from other roles
PAUSED_FIRST = "multi-agent panel, single analyst and post-mortems"


class BudgetExceededError(RuntimeError):
    """Daily or monthly AI budget reached for this role; callers should skip or queue the work.

    Non-tagger roles stop AI_TAGGER_RESERVE_INR before the cap, so the live tagger (and the
    rules engine, which uses no AI) keep running until the reserve is used too."""

    def __init__(self, scope: str, role: str, spent: float, limit: float, cap: float) -> None:
        self.scope, self.role, self.spent, self.limit, self.cap = scope, role, spent, limit, cap
        super().__init__(
            f"{scope} AI budget reached for {role} (Rs {spent:,.0f} of Rs {limit:,.0f})"
        )

    @property
    def tagger_paused(self) -> bool:
        return self.role in RESERVED_ROLES

    def alert_text(self) -> str:
        if self.tagger_paused:
            return (
                f"SYSTEM PROBLEM\nThe {self.scope} AI budget is used up (Rs {self.spent:,.0f} of Rs {self.cap:,.0f}), "
                "including the tagger reserve. Paused: live news tagging too. Still running: rules engine, "
                "paper trading and alerts. AI work resumes when the budget resets."
            )
        return (
            f"SYSTEM PROBLEM\nThe {self.scope} AI budget is nearly used (Rs {self.spent:,.0f} of Rs {self.cap:,.0f}; "
            f"Rs {self.cap - self.limit:,.0f} is kept for the tagger). Paused: {PAUSED_FIRST}. "
            "Still running: live news tagging (until the reserve is used), rules engine, paper trading and alerts."
        )


class AiOutputError(RuntimeError):
    """The model refused or returned output that does not match the schema."""


class MessagesApi(Protocol):
    def parse(self, **kwargs: Any) -> Any: ...


class BatchesApi(Protocol):
    def create(self, **kwargs: Any) -> Any: ...
    def retrieve(self, batch_id: str) -> Any: ...
    def results(self, batch_id: str) -> Any: ...


def price_table(role: str, model: str | None, prompt_tokens: int = 0) -> dict[str, float]:
    """USD per million tokens for this model id (config/ai.yaml `models`), else the role's tier.
    Long-context prices apply when the request's whole prompt is over the model's threshold."""
    cfg = load_yaml("ai.yaml")
    p = dict((cfg.get("models") or {}).get(model or "") or cfg["prices"][cfg["roles"][role]])
    long = p.pop("long_context", None)
    if long and prompt_tokens > int(long["threshold"]):
        p = {k: float(v) for k, v in long.items() if k != "threshold"}
    return {k: float(v) for k, v in p.items()}


def compute_cost_inr(
    role: str,
    input_t: int,
    output_t: int,
    cache_read: int,
    cache_write: int,
    batch: bool = False,
    model: str | None = None,
) -> float:
    """Cost of one call in INR from config/ai.yaml (per model id, else the role's tier).

    `input_t` is the uncached input; cache reads/writes are billed at their own rates.
    """
    cfg = load_yaml("ai.yaml")
    p = price_table(role, model, input_t + cache_read + cache_write)
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
class CallUsage:
    model: str
    input_tokens: int
    output_tokens: int  # includes thinking tokens
    cost_inr: float
    cache_read: int = 0
    cache_write: int = 0
    duration_s: float = 0.0
    effort: str | None = None


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
        env_role = MODEL_ROLE.get(role, role)
        model = str(getattr(self.settings, f"ai_model_{env_role}"))
        if not model:
            raise RuntimeError(f"AI_MODEL_{env_role.upper()} is not set")
        return model

    def effort_for(self, role: str) -> str | None:
        """Per-role effort: a config-table override `ai.effort.<role>` (set by the smoke test's
        auto-fixes or the owner) wins over config/ai.yaml `agents.effort`."""
        from app.db.config_store import get_config

        try:
            with session_scope(self.engine) as s:
                override = get_config(s, f"ai.effort.{role}")
        except Exception:  # noqa: BLE001 - no DB (e.g. one-off scripts): use the YAML value
            override = None
        if override:
            return str(override)
        value = (load_yaml("ai.yaml").get("agents") or {}).get("effort", {}).get(role)
        return str(value) if value else None

    def configured(self, *roles: str) -> bool:
        return all(getattr(self.settings, f"ai_model_{MODEL_ROLE.get(r, r)}", "") for r in roles)

    def spend_inr(self, since: datetime) -> float:
        with session_scope(self.engine) as s:
            return float(
                s.scalar(
                    select(func.coalesce(func.sum(AiUsage.cost_inr), 0)).where(AiUsage.ts >= since)
                )
                or 0
            )

    def check_budget(self, role: str = "analyst", now: datetime | None = None) -> None:
        """Raise BudgetExceededError when this role may not spend more today / this month.
        Every role except the tagger stops AI_TAGGER_RESERVE_INR short of each cap."""
        now = now or datetime.now(IST)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        reserve = 0.0 if role in RESERVED_ROLES else float(self.settings.ai_tagger_reserve_inr)
        for scope, since, cap in (
            ("daily", day_start, float(self.settings.ai_daily_budget_inr)),
            ("monthly", month_start, float(self.settings.ai_monthly_budget_inr)),
        ):
            spent = self.spend_inr(since)
            limit = max(cap - reserve, 0.0)
            if spent >= limit:
                raise BudgetExceededError(scope, role, spent, limit, cap)

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
        return self.parse_with_usage(role, system, user, output, max_tokens, thinking)[0]

    def parse_with_usage(
        self,
        role: str,
        system: str,
        user: str,
        output: type[T],
        max_tokens: int,
        thinking: dict[str, Any] | None = None,
    ) -> tuple[T, "CallUsage"]:
        """`parse` plus the call's tokens and cost (stored with each agent's output)."""
        self.check_budget(role)
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
        effort = self.effort_for(role)
        if effort:
            kwargs["output_config"] = {"effort": effort}
        t0 = time.monotonic()
        resp = self._messages.parse(**kwargs)
        usage = replace(
            self._record_usage(role, model, resp.usage),
            duration_s=round(time.monotonic() - t0, 2),
            effort=effort,
        )
        if resp.stop_reason == "refusal":
            raise AiOutputError("model refused the request")
        if resp.parsed_output is None:
            raise AiOutputError(f"no valid output (stop_reason={resp.stop_reason})")
        parsed: T = resp.parsed_output
        return parsed, usage

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
        self.check_budget(role)
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

    def _record_usage(self, role: str, model: str, usage: Any, batch: bool = False) -> "CallUsage":
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = compute_cost_inr(
            role, usage.input_tokens, usage.output_tokens, cache_read, cache_write, batch, model
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
        return CallUsage(
            model,
            int(usage.input_tokens),
            int(usage.output_tokens),
            cost,
            int(cache_read),
            int(cache_write),
        )
