"""Housekeeping to stay inside the Supabase free-tier storage limit."""

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, delete, select
from sqlalchemy.orm import Session

from app.config import IST
from app.db.config_store import get_config
from app.db.models import Alert, JobRun, OptionQuote, PaperTrade, Signal
from app.db.session import session_scope
from app.history.archive import ARCHIVED_KEY, archive_option_quotes
from app.signals.lifecycle import LIVE
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
    archive_option_quotes(engine)  # always archive full resolution before thinning
    return int(a + b) + thin_option_quotes(engine, now)


def thin_option_quotes(engine: Engine | None, now: datetime) -> int:
    """Keep full-resolution option quotes for N days; older ones keep the LAST quote per
    (symbol, expiry, strike, type, day), which is enough for IV history (free-tier storage)."""
    cfg = load_yaml("options.yaml")["polling"]
    cutoff = now - timedelta(days=int(cfg["store_full_res_days"]))
    removed = 0
    with session_scope(engine) as s:
        archived_upto = int(get_config(s, ARCHIVED_KEY, 0) or 0)
        protected = protected_symbols(s, now, int(cfg["protect_days_after_close"]))
        rows = s.execute(
            select(
                OptionQuote.id,
                OptionQuote.symbol,
                OptionQuote.expiry,
                OptionQuote.strike,
                OptionQuote.type,
                OptionQuote.ts,
            )
            .where(OptionQuote.ts < cutoff, OptionQuote.id <= archived_upto)
            .order_by(OptionQuote.ts.desc())
        ).all()
        keep: set[tuple[Any, ...]] = set()
        drop: list[int] = []
        for rid, sym, exp, k, typ, ts in rows:
            if sym in protected:
                continue
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


def protected_symbols(s: Session, now: datetime, days_after: int) -> set[str]:
    """Stocks whose quotes must stay at full resolution: open trades, live signals, and anything
    that closed/cancelled within `days_after` days."""
    since = now - timedelta(days=days_after)
    open_or_recent = s.scalars(
        select(PaperTrade.symbol).where(
            (PaperTrade.status == "OPEN") | (PaperTrade.exit_ts >= since)
        )
    ).all()
    signals = s.scalars(
        select(Signal.symbol).where(Signal.status.in_(LIVE) | (Signal.updated_at >= since))
    ).all()
    return {x for x in [*open_or_recent, *signals] if x}
