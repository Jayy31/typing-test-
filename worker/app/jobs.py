"""Job runner: every scheduled job runs through run_job (logging, retries, error capture)."""

import logging
import time
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine

from app.config import IST
from app.db.models import JobRun
from app.db.session import session_scope
from app.logsafe import redact

log = logging.getLogger(__name__)

# A job returns the number of items it processed.
JobFn = Callable[[], int]


def run_job(
    name: str,
    fn: JobFn,
    *,
    engine: Engine | None = None,
    retries: int = 2,
    backoff_s: float = 2.0,
) -> bool:
    """Run fn with retries and exponential backoff; record the outcome in job_runs.

    Never raises: a failing job must not take down the scheduler. Returns True on success.
    """
    started = datetime.now(IST)
    with session_scope(engine) as s:
        run = JobRun(job=name, started_at=started, status="running")
        s.add(run)
        s.flush()
        run_id = run.id

    error: str | None = None
    items: int | None = None
    for attempt in range(retries + 1):
        try:
            items = fn()
            error = None
            break
        except Exception as exc:  # noqa: BLE001 - job boundary
            error = redact(f"{type(exc).__name__}: {exc}")
            log.warning("job %s attempt %d failed: %s", name, attempt + 1, error)
            if attempt < retries:
                time.sleep(backoff_s * 2**attempt)

    with session_scope(engine) as s:
        finished = s.get(JobRun, run_id)
        assert finished is not None
        finished.finished_at = datetime.now(IST)
        finished.status = "error" if error else "ok"
        finished.error = error
        finished.items_processed = items
    return error is None
