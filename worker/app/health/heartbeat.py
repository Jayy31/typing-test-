"""Health checks over job_runs: last success and consecutive failures per job."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, select

from app.db.models import JobRun
from app.db.session import session_scope


@dataclass(frozen=True)
class JobHealth:
    job: str
    last_success: datetime | None
    consecutive_failures: int
    last_error: str | None


def job_health(engine: Engine | None = None, recent: int = 20) -> list[JobHealth]:
    """Health of every job, from its most recent finished runs."""
    with session_scope(engine) as s:
        jobs = s.scalars(select(JobRun.job).distinct()).all()
        out: list[JobHealth] = []
        for job in sorted(jobs):
            runs = s.scalars(
                select(JobRun)
                .where(JobRun.job == job, JobRun.status != "running")
                .order_by(JobRun.id.desc())
                .limit(recent)
            ).all()
            failures = 0
            for r in runs:
                if r.status == "ok":
                    break
                failures += 1
            last_ok = next((r.finished_at for r in runs if r.status == "ok"), None)
            last_err = next((r.error for r in runs if r.status == "error"), None)
            out.append(JobHealth(job, last_ok, failures, last_err))
        return out


def failing_jobs(threshold: int, engine: Engine | None = None) -> list[JobHealth]:
    """Jobs that failed `threshold` or more times in a row."""
    return [h for h in job_health(engine) if h.consecutive_failures >= threshold]
