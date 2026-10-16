"""Housekeeping to stay inside the Supabase free-tier storage limit."""

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, delete, select

from app.config import IST
from app.db.models import Alert, JobRun, OptionQuote
from app.db.session import session_scope
from app.timeutil import as_ist
from app.yaml_config import load_yaml


def cleanup(engine: Engine | None = None) -> int:
    """Drop old successful job_runs (errors are kept) and delivered alerts."""
    now = datetime.now(IST)
    old_runs = delete(JobRun).where(
        JobRun.status == "ok", JobRun.started_at < now - timedelta(days=14)
    )
    old_alerts = delete(Alert).where(
        Alert.status != "pending", Alert.created_at < now - timedelta(days=90)
    )
    with session_scope(engine) as s:
        a = cast(CursorResult[Any], s.execute(old_runs)).rowcount
        b = cast(CursorResult[Any], s.execute(old_alerts)).rowcount
    return int(a + b) + thin_option_quotes(engine, now)


def thin_option_quotes(engine: Engine | None, now: datetime) -> int:
    """Keep full-resolution option quotes for N days; older ones keep the LAST quote per
    (symbol, expiry, strike, type, day), which is enough for IV history (free-tier storage)."""
    days = int(load_yaml("options.yaml")["polling"]["store_full_res_days"])
    cutoff = now - timedelta(days=days)
    removed = 0
    with session_scope(engine) as s:
        rows = s.execute(
            select(
                OptionQuote.id,
                OptionQuote.symbol,
                OptionQuote.expiry,
                OptionQuote.strike,
                OptionQuote.type,
                OptionQuote.ts,
            )
            .where(OptionQuote.ts < cutoff)
            .order_by(OptionQuote.ts.desc())
        ).all()
        keep: set[tuple[Any, ...]] = set()
        drop: list[int] = []
        for rid, sym, exp, k, typ, ts in rows:
            key = (sym, exp, k, typ, as_ist(ts).date())
            if key in keep:
                drop.append(rid)
            else:
                keep.add(key)
        for i in range(0, len(drop), 1000):
            removed += cast(
                CursorResult[Any],
                s.execute(delete(OptionQuote).where(OptionQuote.id.in_(drop[i : i + 1000]))),
            ).rowcount
    return removed
