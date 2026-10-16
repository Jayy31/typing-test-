"""All job schedules live here. One worker process, APScheduler inside."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.collectors.fno_meta import refresh_fno_universe
from app.config import IST, get_settings
from app.health.heartbeat import failing_jobs
from app.jobs import run_job

log = logging.getLogger(__name__)


def heartbeat() -> int:
    """Log jobs that keep failing. (Telegram delivery for this arrives in Phase 2.)"""
    bad = failing_jobs(get_settings().job_failure_alert_threshold)
    for h in bad:
        log.error(
            "job %s failed %d times in a row: %s", h.job, h.consecutive_failures, h.last_error
        )
    return len(bad)


def build_scheduler() -> BlockingScheduler:
    sched = BlockingScheduler(timezone=IST)
    sched.add_job(
        run_job,
        IntervalTrigger(minutes=5),
        args=["heartbeat", heartbeat],
        id="heartbeat",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        run_job,
        CronTrigger(hour=7, minute=30, timezone=IST),
        args=["fno_meta", refresh_fno_universe],
        id="fno_meta",
        max_instances=1,
        coalesce=True,
    )
    return sched


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    log.info("worker starting")
    build_scheduler().start()


if __name__ == "__main__":
    main()
