"""Lab AI (Phase 14): roles, budget (lab caps, paused first) and a small tool loop.

Roles come from config/strategy_lab.yaml `roles` with env overrides LAB_<ROLE>_<FIELD> and the
same provider-neutral layer and AI_ALLOWED_MODELS check as the research agent. Lab AI sees only
PRE-cutoff statistics and Stage-0 / plateau / cost trials (never holdout results), proposes or
refines specs through a strict `submit` tool, and has no tool that writes anything.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.client import AiClient, BudgetExceededError
from app.ai.providers import get_provider
from app.ai.providers.base import ModelProvider, ToolResult, ToolSpec, Usage, WebTools
from app.config import IST
from app.db.models import AiUsage, LabHypothesis, LabSpec, LabTrial, ResearchRun
from app.lab.spec import cfg
from app.research import runs
from app.research.config import RoleConfig
from app.research.costing import cost_inr

log = logging.getLogger(__name__)
ROLES = ("lab_hypothesis", "lab_summary")
RUN_KINDS = ("lab_hypothesis", "lab_summary")


def role_config(role: str) -> RoleConfig:
    if role not in ROLES:
        raise ValueError(f"unknown lab role {role}")
    base = dict(cfg()["roles"][role])
    suffix = role.removeprefix("lab_").upper()
    for f in fields(RoleConfig):
        v = os.environ.get(f"LAB_{suffix}_{f.name.upper()}")
        if v not in (None, ""):
            base[f.name] = v
    return RoleConfig(
        role=role,
        preset="lab",
        provider=str(base.get("provider", "anthropic")),
        model=str(base["model"]),
        effort=str(base["effort"]) if base.get("effort") else None,
        web=False,
        max_tool_calls=int(base.get("max_tool_calls", 0)),
        max_web_searches=0,
        max_web_fetches=0,
        max_input_tokens=int(base["max_input_tokens"]),
        max_output_tokens=int(base["max_output_tokens"]),
        max_inr=float(base["max_inr"]),
        timeout_s=float(base["timeout_s"]),
    )


def spent(s: Session, since: datetime) -> float:
    return float(
        s.scalar(
            select(func.coalesce(func.sum(ResearchRun.cost_inr), 0)).where(
                ResearchRun.kind.in_(RUN_KINDS), ResearchRun.created_at >= since
            )
        )
        or 0
    )


def proposal_reserve(s: Session, now: datetime) -> float:
    """Rs kept free in the lab's monthly cap for the AI proposals still due this month
    (`ai_proposals_per_week` per week): refinements and write-ups must leave it unspent."""
    per_week = int(cfg()["schedule"]["ai_proposals_per_week"])
    week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    done = int(
        s.scalar(
            select(func.count(LabHypothesis.id)).where(
                LabHypothesis.source == "ai_proposal", LabHypothesis.registered_at >= week
            )
        )
        or 0
    )
    nxt = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
    later_weeks = (max((nxt.date() - (week + timedelta(days=7)).date()).days, 0) + 6) // 7
    due = max(per_week - done, 0) + per_week * later_weeks
    return due * float(cfg()["estimates_inr"]["lab_hypothesis"])


def check_budget(
    s: Session, ai: AiClient, role: str, now: datetime | None = None, reserve_inr: float = 0.0
) -> None:
    """Lab caps (Rs 30/day, Rs 600/month), and the lab pauses FIRST: it stops `pause_first_headroom`
    below the overall caps (on top of the tagger reserve). `reserve_inr` is kept free in the lab's
    monthly cap (see `proposal_reserve`)."""
    now = now or datetime.now(IST)
    est = float(cfg()["estimates_inr"][role])
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    caps, head = cfg()["budgets"], cfg()["pause_first_headroom"]
    st = ai.settings
    reserve = float(st.ai_tagger_reserve_inr)
    for scope, since, overall, lab_cap, room in (
        (
            "daily",
            day,
            float(st.ai_daily_budget_inr),
            float(caps["daily_inr"]),
            float(head["daily_inr"]),
        ),
        (
            "monthly",
            month,
            float(st.ai_monthly_budget_inr),
            float(caps["monthly_inr"]),
            float(head["monthly_inr"]),
        ),
    ):
        total = float(
            s.scalar(
                select(func.coalesce(func.sum(AiUsage.cost_inr), 0)).where(AiUsage.ts >= since)
            )
            or 0
        )
        limit = max(overall - reserve - room, 0.0)
        if total + est > limit:
            raise BudgetExceededError(scope, "strategy lab (paused first)", total, limit, overall)
        lab = spent(s, since) + (reserve_inr if scope == "monthly" else 0.0)
        if lab + est > lab_cap:
            raise BudgetExceededError(scope, "strategy lab", lab, lab_cap, lab_cap)


# ---- read-only lab tools ------------------------------------------------------------------
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatsIn(_In):
    event_type: str
    sector: str | None = None
    window: int = Field(default=3, ge=1, le=10)


class NameIn(_In):
    name: str


class NoIn(_In):
    pass


@dataclass
class LabToolContext:
    session: Session
    stats: dict[Any, Any]  # pre-cutoff analogue groups (summarize output), may be empty


def tool_event_stats(ctx: LabToolContext, a: StatsIn) -> Any:
    out = []
    for (etype, sec, sym, reg, w), v in ctx.stats.items():
        if etype != a.event_type or w != a.window or sym is not None:
            continue
        if a.sector is not None and sec != a.sector:
            continue
        out.append(
            {
                "sector": sec,
                "regime": reg,
                "n": v[0],
                "mean_abn_pct": round(v[2], 2),
                "hit_rate": round(v[4], 3),
            }
        )
    return {
        "note": "pre-cutoff event study, direction-adjusted abnormal % (positive = moved as "
        "the news suggested)",
        "rows": sorted(out, key=lambda r: -r["n"])[:20],
    }


def tool_hypotheses(ctx: LabToolContext, a: NoIn) -> Any:
    rows = ctx.session.scalars(
        select(LabHypothesis).order_by(LabHypothesis.id.desc()).limit(40)
    ).all()
    return [
        {
            "name": h.name,
            "source": h.source,
            "status": h.status,
            "expected_effect": h.expected_effect[:160],
        }
        for h in rows
    ]


def tool_trials(ctx: LabToolContext, a: NameIn) -> Any:
    h = ctx.session.scalars(select(LabHypothesis).where(LabHypothesis.name == a.name)).first()
    if h is None:
        raise ValueError(f"unknown hypothesis {a.name}")
    out = []
    for sp in ctx.session.scalars(select(LabSpec).where(LabSpec.hypothesis_id == h.id)):
        for t in ctx.session.scalars(
            select(LabTrial).where(
                LabTrial.spec_id == sp.id, LabTrial.kind.in_(("stage0", "plateau", "cost"))
            )
        ):
            out.append(
                {
                    "spec_version": sp.version,
                    "kind": t.kind,
                    "label": t.label,
                    "n": t.n_trades,
                    "events": t.n_events,
                    "expectancy_pct": t.expectancy_pct,
                    "ci": [t.ci_lo, t.ci_hi],
                    "diagnostics": (t.metrics_json or {}).get("diagnostics"),
                }
            )
    return {
        "specs": [
            s.spec_yaml
            for s in ctx.session.scalars(select(LabSpec).where(LabSpec.hypothesis_id == h.id))
        ][-3:],
        "trials": out[-30:],
    }


def tool_vocabulary(ctx: LabToolContext, a: NoIn) -> Any:
    from app import taxonomy

    return {
        "event_types": taxonomy.event_types() + list(taxonomy.structured_types()),
        "regimes": ["calm", "normal", "stressed", "risk_on", "risk_off"],
        "strike": cfg()["spec"]["strike_modes"],
        "entry": ["event_close", "next_open", "before_event (scheduled results only)"],
        "core_rules": "stock options only, buy calls (bullish) / puts (bearish) only, one leg, "
        "monthly expiry chosen by the system",
    }


@dataclass(frozen=True)
class LabTool:
    description: str
    schema: type[_In]
    fn: Any


TOOLS: dict[str, LabTool] = {
    "get_event_stats": LabTool(
        "Pre-cutoff event-study base rates for an event type.", StatsIn, tool_event_stats
    ),
    "get_hypotheses": LabTool(
        "Hypotheses already registered (avoid duplicates).", NoIn, tool_hypotheses
    ),
    "get_trials": LabTool(
        "Stage-0, plateau and cost-stress trials of a hypothesis (no holdout).", NameIn, tool_trials
    ),
    "get_vocabulary": LabTool("Allowed spec vocabulary and the core rules.", NoIn, tool_vocabulary),
}


def tool_specs() -> list[ToolSpec]:
    return [ToolSpec(n, t.description, t.schema) for n, t in TOOLS.items()]


def call(name: str, args: dict[str, Any], ctx: LabToolContext) -> tuple[str, bool]:
    t = TOOLS.get(name)
    if t is None:
        return f"error: unknown tool {name!r}", True
    try:
        out = t.fn(ctx, t.schema.model_validate(args))
    except (ValidationError, ValueError) as exc:
        return f"error: {exc}"[:600], True
    return json.dumps(out, default=str)[:8000], False


# ---- loop -----------------------------------------------------------------------------------
@dataclass
class LoopResult:
    data: BaseModel | None
    run_id: int
    status: str
    error: str | None
    cost_inr: float


def run_loop(
    s: Session,
    rc: RoleConfig,
    kind: str,
    system: str,
    user: str,
    out_schema: type[BaseModel],
    ctx: LabToolContext,
    subject: str,
    provider: ModelProvider | None = None,
) -> LoopResult:
    """Tools then `submit`; one retry on an invalid submission; limits from the role config."""
    provider = provider or get_provider(rc.provider)
    run = runs.new_run(s, kind, rc, subject=subject[:96], created_at=datetime.now(IST))
    run.estimate_inr = float(cfg()["estimates_inr"][rc.role])
    tools = (tool_specs() if rc.max_tool_calls else []) + [
        ToolSpec("submit", "Submit the final answer (call exactly once).", out_schema)
    ]
    conv = provider.start(
        model=rc.model,
        system=system,
        tools=tools,
        web=WebTools(),
        max_tokens=rc.max_output_tokens,
        effort=rc.effort,
        timeout_s=rc.timeout_s,
    )
    total, cost, calls, started = Usage(), 0.0, 0, time.monotonic()
    text: str | None = user
    results: list[ToolResult] = []
    answer: BaseModel | None = None
    error: str | None = None
    retried = False
    for _ in range(rc.max_tool_calls + 4):
        try:
            turn = conv.send(text, results or None)
        except Exception as exc:  # noqa: BLE001
            error = f"provider error: {type(exc).__name__}"
            break
        text, results = None, []
        total.accumulate(turn.usage)
        cost += cost_inr(rc.role, rc.model, turn.usage)
        runs.charge(s, run, rc, turn.usage)
        sub = [c for c in turn.tool_calls if c.name == "submit"]
        if sub:
            try:
                answer = out_schema.model_validate(sub[0].input)
                break
            except ValidationError as exc:
                if retried:
                    error = f"invalid answer after retry: {exc.errors()[:2]}"
                    break
                retried = True
                results = [
                    ToolResult(
                        sub[0].id, f"Rejected: {exc.errors()[:3]}. Fix and submit again.", True
                    )
                ]
                continue
        others = [c for c in turn.tool_calls if c.name != "submit"]
        if not others:
            if retried or turn.stop != "end":
                error = error or f"no answer (stop={turn.stop})"
                break
            retried = True
            text = "Call submit now with your final answer."
            continue
        for c in others:
            calls += 1
            body, bad = call(c.name, c.input, ctx)
            results.append(ToolResult(c.id, body, bad))
        if (
            calls >= rc.max_tool_calls
            or cost >= rc.max_inr
            or time.monotonic() - started > rc.timeout_s
        ):
            text = "LIMIT REACHED. Call submit now with your best answer."
    run.status = "ok" if answer is not None else "failed"
    run.error, run.tool_calls = error, calls
    run.duration_s = round(time.monotonic() - started, 2)
    run.output_json = answer.model_dump(mode="json") if answer is not None else None
    return LoopResult(answer, run.id, run.status, error, run.cost_inr)


def since_days(days: int) -> datetime:
    return datetime.now(IST) - timedelta(days=days)
