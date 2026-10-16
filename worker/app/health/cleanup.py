"""Housekeeping to stay inside the Supabase free-tier storage limit."""

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, delete

from app.config import IST
from app.db.models import Alert, JobRun
from app.db.session import session_scope


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
    return int(a + b)
