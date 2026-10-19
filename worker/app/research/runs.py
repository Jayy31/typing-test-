"""Research-agent runs: budgets (sub-caps inside the overall AI budget), cost logging and the
per-role overrun alert.

Every research request is written to `ai_usage` (so the overall AI_DAILY/MONTHLY_BUDGET_INR and
the tagger reserve keep working) and summed on its `research_runs` row (for the sub-caps in
config/research_agent.yaml `budgets` and the dashboard).
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.ai.client import AiClient, BudgetExceededError
from app.ai.providers.base import Usage
from app.config import IST
from app.db.models import AiUsage, ResearchRun, ResearchToolCall
from app.research.config import RoleConfig, cfg
from app.research.costing import cost_inr

# budget group per run kind (live + top share the live caps)
GROUP = {"files": "files", "live": "live", "review": "review", "smoke": None}


def spent_inr(s: Session, group: str, since: datetime) -> float:
    kinds = [k for k, g in GROUP.items() if g == group]
    return float(
        s.scalar(
            select(func.coalesce(func.sum(ResearchRun.cost_inr), 0)).where(
                ResearchRun.kind.in_(kinds), ResearchRun.created_at >= since
            )
        )
        or 0
    )


def check_budget(
    s: Session, ai: AiClient, kind: str, role: str, estimate: float, now: datetime | None = None
) -> None:
    """Raise BudgetExceededError when the overall budget (minus the tagger reserve) or this
    kind's daily/monthly sub-cap cannot take one more run of about `estimate` rupees."""
    now = now or datetime.now(IST)
    _overall(s, ai, role, now)
    group = GROUP.get(kind)
    if group is None:
        return
    caps = cfg()["budgets"][group]
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for scope, since, cap in (
        ("daily", day, float(caps["daily_inr"])),
        ("monthly", day.replace(day=1), float(caps["monthly_inr"])),
    ):
        spent = spent_inr(s, group, since)
        if spent + estimate > cap:
            raise BudgetExceededError(scope, f"research {group}", spent, cap, cap)


def _overall(s: Session, ai: AiClient, role: str, now: datetime) -> None:
    """AiClient.check_budget in the caller's session: the overall caps minus the tagger reserve."""
    st = ai.settings
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    reserve = float(st.ai_tagger_reserve_inr)
    for scope, since, cap in (
        ("daily", day, float(st.ai_daily_budget_inr)),
        ("monthly", day.replace(day=1), float(st.ai_monthly_budget_inr)),
    ):
        spent = float(
            s.scalar(
                select(func.coalesce(func.sum(AiUsage.cost_inr), 0)).where(AiUsage.ts >= since)
            )
            or 0
        )
        limit = max(cap - reserve, 0.0)
        if spent >= limit:
            raise BudgetExceededError(scope, role, spent, limit, cap)


def new_run(s: Session, kind: str, rc: RoleConfig, **fields: Any) -> ResearchRun:
    est = float((cfg().get("estimates_inr") or {}).get(rc.role, 0) or 0) or None
    run = ResearchRun(
        kind=kind,
        role=rc.role,
        preset=rc.preset,
        provider=rc.provider,
        model=rc.model,
        effort=rc.effort,
        status=fields.pop("status", "running"),
        estimate_inr=est,
        created_at=fields.pop("created_at", datetime.now(IST)),
        **fields,
    )
    s.add(run)
    s.flush()
    return run


def charge(s: Session, run: ResearchRun, rc: RoleConfig, u: Usage, batch: bool = False) -> float:
    """Add one request's usage to the run and to ai_usage. Returns its cost in INR."""
    c = cost_inr(rc.role, rc.model, u, batch)
    run.input_tokens = (run.input_tokens or 0) + u.input_tokens + u.cache_write
    run.output_tokens = (run.output_tokens or 0) + u.output_tokens
    run.cache_read_tokens = (run.cache_read_tokens or 0) + u.cache_read
    run.web_searches = (run.web_searches or 0) + u.web_searches
    run.web_fetches = (run.web_fetches or 0) + u.web_fetches
    run.cost_inr = round((run.cost_inr or 0) + c, 4)
    s.add(
        AiUsage(
            ts=datetime.now(IST),
            model=rc.model,
            input_tokens=u.input_tokens + u.cache_write,
            output_tokens=u.output_tokens,
            cached_tokens=u.cache_read,
            cost_inr=c,
        )
    )
    return c


def log_tool_call(
    s: Session,
    run_id: int,
    seq: int,
    tool: str,
    args: dict[str, Any] | None,
    rows: int | None,
    is_error: bool,
    summary: str,
    duration_ms: int | None,
) -> None:
    s.add(
        ResearchToolCall(
            run_id=run_id,
            seq=seq,
            tool=tool,
            args_json=args,
            rows=rows,
            is_error=is_error,
            summary=summary[:500],
            duration_ms=duration_ms,
        )
    )


def overrun_check(engine: Engine | None = None, now: datetime | None = None) -> int:
    """Health alert when a role's average actual cost is more than `pct` above its estimate over
    the last `days` (at least `min_runs` runs). One alert per role per week."""
    from app.db.session import session_scope
    from app.notify.outbox import enqueue_alert

    now = now or datetime.now(IST)
    oc = cfg()["overrun_alert"]
    since = now - timedelta(days=int(oc["days"]))
    sent = 0
    with session_scope(engine) as s:
        rows = s.execute(
            select(
                ResearchRun.role,
                func.count(ResearchRun.id),
                func.avg(ResearchRun.cost_inr),
                func.avg(ResearchRun.estimate_inr),
            )
            .where(
                ResearchRun.created_at >= since,
                ResearchRun.estimate_inr.is_not(None),
                ResearchRun.status.in_(("ok", "limited", "failed")),
            )
            .group_by(ResearchRun.role)
        ).all()
        for role, n, avg_cost, avg_est in rows:
            if n < int(oc["min_runs"]) or not avg_est:
                continue
            over = (float(avg_cost) / float(avg_est) - 1) * 100
            if over > float(oc["pct"]):
                text = (
                    f"SYSTEM PROBLEM\nResearch agent role {role} costs more than estimated: "
                    f"average Rs {float(avg_cost):.2f} per run vs estimate Rs {float(avg_est):.2f} "
                    f"(+{over:.0f}%) over {n} runs in {oc['days']} days. Check the role's limits "
                    "in config/research_agent.yaml or update estimates_inr."
                )
                week = now.strftime("%G-W%V")
                sent += enqueue_alert(s, "health", text, f"research_overrun:{role}:{week}")
    return sent
