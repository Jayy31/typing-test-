"""All job schedules live here. One worker process, APScheduler inside."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.ai.tagger import tag_items
from app.collectors.calendar import sync_manual_calendar
from app.collectors.fno_meta import refresh_fno_universe
from app.collectors.macro import collect_macro
from app.collectors.news_rss import collect_news
from app.collectors.nse import collect_announcements, collect_event_calendar, collect_fii_dii
from app.config import IST, get_settings
from app.health.heartbeat import failing_jobs
from app.jobs import JobFn, run_job
from app.processing.pipeline import gate_news

log = logging.getLogger(__name__)


def heartbeat() -> int:
    """Log jobs that keep failing. (Telegram delivery for this arrives in Phase 2.)"""
    bad = failing_jobs(get_settings().job_failure_alert_threshold)
    for h in bad:
        log.error(
            "job %s failed %d times in a row: %s", h.job, h.consecutive_failures, h.last_error
        )
    return len(bad)


def _job(sched: BlockingScheduler, name: str, fn: JobFn, trigger: BaseTrigger) -> None:
    sched.add_job(
        run_job,
        trigger,
        args=[name, fn],
        id=name,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )


def build_scheduler() -> BlockingScheduler:
    s = get_settings()
    sched = BlockingScheduler(timezone=IST)
    _job(sched, "heartbeat", heartbeat, IntervalTrigger(minutes=5))
    _job(sched, "fno_meta", refresh_fno_universe, CronTrigger(hour=7, minute=30, timezone=IST))
    # News: every NEWS_POLL_MIN during 07:00-23:00 IST, every 30 min overnight (global news).
    day = f"*/{s.news_poll_min}"
    _job(sched, "news_day", collect_news, CronTrigger(hour="7-22", minute=day, timezone=IST))
    _job(sched, "news_night", collect_news, CronTrigger(hour="23,0-6", minute="0,30", timezone=IST))
    _job(
        sched,
        "nse_announcements",
        collect_announcements,
        CronTrigger(hour="7-22", minute="*/10", timezone=IST),
    )
    _job(sched, "gate_news", gate_news, IntervalTrigger(minutes=2))
    _job(sched, "tag_news", tag_items, IntervalTrigger(minutes=3))
    _job(sched, "macro", collect_macro, IntervalTrigger(minutes=15))
    _job(sched, "fii_dii", collect_fii_dii, CronTrigger(hour=19, minute=0, timezone=IST))
    _job(
        sched,
        "event_calendar",
        collect_event_calendar,
        CronTrigger(hour=7, minute=45, timezone=IST),
    )
    _job(
        sched, "manual_calendar", sync_manual_calendar, CronTrigger(hour=7, minute=40, timezone=IST)
    )
    return sched


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    log.info("worker starting")
    build_scheduler().start()


if __name__ == "__main__":
    main()
