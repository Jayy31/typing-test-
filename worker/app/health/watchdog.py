"""Watchdog (Phase 10): stuck job runs, external heartbeat ping, database size, AI cost report."""

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine, func, select, text

from app.config import IST, get_settings
from app.db.config_store import set_config
from app.db.models import AiUsage, JobRun
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert
from app.timeutil import as_ist
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


def stuck_runs(
    engine: Engine | None, intervals_min: dict[str, float], now: datetime | None = None
) -> list[str]:
    """Runs still 'running' after stuck_factor x their interval (min stuck_floor_minutes): marked
    'stuck' so the job's next run is not blocked forever, and reported."""
    c = load_yaml("hardening.yaml")["watchdog"]
    now = now or datetime.now(IST)
    out = []
    with session_scope(engine) as s:
        for r in s.scalars(select(JobRun).where(JobRun.status == "running")):
            limit = max(
                float(c["stuck_floor_minutes"]),
                float(c["stuck_factor"]) * intervals_min.get(r.job, 20.0),
            )
            if now - as_ist(r.started_at) > timedelta(minutes=limit):
                r.status, r.error, r.finished_at = "stuck", f"no finish after {limit:.0f} min", now
                out.append(f"{r.job} (started {as_ist(r.started_at):%d-%b %H:%M})")
    return out


def ping(http: httpx.Client | None = None) -> bool:
    """healthchecks.io-style ping: the external service alerts if the pings stop (VM down)."""
    url = get_settings().healthcheck_ping_url
    if not url:
        return False
    try:
        (http or httpx).get(url, timeout=10.0)
    except httpx.HTTPError as exc:
        log.warning("healthcheck ping failed: %s", type(exc).__name__)
        return False
    return True


def db_size(engine: Engine | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Postgres size vs the free-tier limit; warns once a day above the warn share."""
    c = load_yaml("hardening.yaml")["db"]
    now = now or datetime.now(IST)
    with session_scope(engine) as s:
        if s.get_bind().dialect.name != "postgresql":
            return {}
        total = (
            float(s.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0)
            / 1e6
        )
        rows = s.execute(
            text(
                "SELECT relname, pg_total_relation_size(relid) "
                "FROM pg_catalog.pg_statio_user_tables "
                "ORDER BY 2 DESC LIMIT 8"
            )
        ).all()
        res = {
            "total_mb": round(total, 1),
            "limit_mb": c["limit_mb"],
            "largest": {r[0]: round(r[1] / 1e6, 1) for r in rows},
        }
        set_config(s, "db_size", res)
        if total > float(c["limit_mb"]) * float(c["warn_share"]):
            enqueue_alert(
                s,
                "health",
                f"DATABASE SIZE\n{total:.0f} MB of {c['limit_mb']} MB used. Largest: "
                + ", ".join(f"{k} {v} MB" for k, v in res["largest"].items()),
                f"dbsize:{now.date()}",
            )
    return res


def cost_report(engine: Engine | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Month-to-date spend and prompt-cache hit rate per model; models over their estimate share."""
    now = now or datetime.now(IST)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with session_scope(engine) as s:
        rows = s.execute(
            select(
                AiUsage.model,
                func.sum(AiUsage.cost_inr),
                func.sum(AiUsage.input_tokens),
                func.sum(AiUsage.cached_tokens),
                func.count(),
            )
            .where(AiUsage.ts >= start)
            .group_by(AiUsage.model)
        ).all()
        out = {
            m: {
                "cost_inr": round(float(c or 0), 1),
                "calls": int(n),
                "cache_hit_rate": round(float(cached or 0) / float(inp + (cached or 0)), 3)
                if inp
                else None,
            }
            for m, c, inp, cached, n in rows
        }
        set_config(s, "ai_cost_report", {"month": start.date().isoformat(), "models": out})
    return out
