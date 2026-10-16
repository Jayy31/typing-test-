from sqlalchemy import Engine, select

from app.db.models import JobRun
from app.db.session import session_scope
from app.health.heartbeat import failing_jobs, job_health
from app.jobs import run_job


def _runs(engine: Engine) -> list[JobRun]:
    with session_scope(engine) as s:
        return list(s.scalars(select(JobRun).order_by(JobRun.id)))


def test_success_is_logged(engine: Engine) -> None:
    assert run_job("ok_job", lambda: 7, engine=engine) is True
    (run,) = _runs(engine)
    assert (run.status, run.items_processed, run.error) == ("ok", 7, None)
    assert run.finished_at is not None


def test_failure_is_logged_not_raised(engine: Engine) -> None:
    def boom() -> int:
        raise ValueError("bad feed")

    assert run_job("bad_job", boom, engine=engine, retries=0) is False
    (run,) = _runs(engine)
    assert run.status == "error" and run.error == "ValueError: bad feed"


def test_retries_then_succeeds(engine: Engine) -> None:
    calls = {"n": 0}

    def flaky() -> int:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("temporary")
        return 1

    assert run_job("flaky", flaky, engine=engine, retries=2, backoff_s=0) is True
    assert calls["n"] == 3
    assert len(_runs(engine)) == 1  # one job_runs row per run, not per attempt


def test_health_counts_consecutive_failures(engine: Engine) -> None:
    def boom() -> int:
        raise RuntimeError("down")

    run_job("j", lambda: 1, engine=engine)
    for _ in range(3):
        run_job("j", boom, engine=engine, retries=0)
    (h,) = job_health(engine)
    assert h.consecutive_failures == 3 and h.last_success is not None
    assert [x.job for x in failing_jobs(3, engine)] == ["j"]
    assert failing_jobs(4, engine) == []
