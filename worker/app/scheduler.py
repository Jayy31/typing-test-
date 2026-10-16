"""All job schedules live here. One worker process, APScheduler inside."""

import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine

from app.ai.tagger import tag_items
from app.collectors.calendar import sync_manual_calendar
from app.collectors.fno_meta import refresh_fno_universe
from app.collectors.macro import collect_macro
from app.collectors.news_rss import collect_news
from app.collectors.nse import (
    collect_announcements,
    collect_corporate_actions,
    collect_event_calendar,
    collect_fii_dii,
    collect_fno_ban,
)
from app.config import IST, get_settings
from app.db.session import session_scope
from app.health.cleanup import cleanup
from app.health.heartbeat import failing_jobs
from app.history import jobs as history_jobs
from app.jobs import JobFn, run_job
from app.market.live import (
    configured_poll_minutes,
    poll_universe_options,
    sync_instruments_job,
)
from app.notify import templates
from app.notify.outbox import enqueue_alert
from app.notify.telegram import poll_telegram_job, send_alerts_job
from app.processing.pipeline import gate_news
from app.signals.service import run_signal_cycle
from app.tracking.counterfactual import track_rejections
from app.tracking.scorecard import update_scores
from app.trading.paper_engine import trading_cycle
from app.trading.portfolio import eod_snapshot_job, snapshot_job
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


def heartbeat(engine: Engine | None = None) -> int:
    """Queue a Telegram alert for jobs that keep failing (at most 4 per job per day)."""
    now = datetime.now(IST)
    bad = failing_jobs(get_settings().job_failure_alert_threshold, engine)
    with session_scope(engine) as s:
        for h in bad:
            log.error(
                "job %s failed %d times in a row: %s", h.job, h.consecutive_failures, h.last_error
            )
            enqueue_alert(
                s,
                "health",
                templates.health_problem(h.job, h.consecutive_failures, h.last_error),
                f"health:{h.job}:{now.date()}:{now.hour // 6}",
            )
        warning = token_expiry_warning(now)
        if warning:
            enqueue_alert(s, "health", warning, f"broker_token:{now.date()}")
    return len(bad)


def token_expiry_warning(now: datetime, warn_days: int = 14) -> str | None:
    """Upstox analytics tokens last one year: warn daily during the last two weeks."""
    issued = get_settings().broker_token_issued_on
    if not issued:
        return None
    expires = date.fromisoformat(issued) + timedelta(days=365)
    left = (expires - now.date()).days
    if left > warn_days:
        return None
    return (
        f"SYSTEM PROBLEM\nUpstox analytics token expires on {expires} ({left} days left). "
        "Generate a new one in the Upstox developer portal and update BROKER_ACCESS_TOKEN and "
        "BROKER_TOKEN_ISSUED_ON."
    )


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
    _job(sched, "signal_cycle", run_signal_cycle, IntervalTrigger(minutes=5))
    _job(sched, "send_alerts", send_alerts_job, IntervalTrigger(seconds=20))
    _job(sched, "telegram_poll", poll_telegram_job, IntervalTrigger(seconds=15))
    _job(sched, "fno_ban", collect_fno_ban, CronTrigger(hour="8,17", minute=15, timezone=IST))
    _job(
        sched,
        "corporate_actions",
        collect_corporate_actions,
        CronTrigger(hour=7, minute=50, timezone=IST),
    )
    _job(sched, "cleanup", cleanup, CronTrigger(hour=3, minute=30, timezone=IST))
    # Live options (Phase 3a, Upstox analytics token; jobs no-op until BROKER_ACCESS_TOKEN is set)
    _job(
        sched,
        "upstox_instruments",
        sync_instruments_job,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=35, timezone=IST),
    )
    # Paper trading: one cycle polls chains for live signals + open trades, enters and exits.
    _job(sched, "trading_cycle", trading_cycle, IntervalTrigger(minutes=configured_poll_minutes()))
    every = int(load_yaml("portfolio.yaml")["snapshot_every_min"])
    _job(sched, "portfolio_snapshot", snapshot_job, IntervalTrigger(minutes=every))
    _job(
        sched,
        "portfolio_eod",
        eod_snapshot_job,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=35, timezone=IST),
    )
    trk = load_yaml("tracking.yaml")
    for hhmm in trk["counterfactual"]["times"]:
        h, m = (int(x) for x in hhmm.split(":"))
        _job(
            sched,
            f"counterfactual_{h:02d}{m:02d}",
            track_rejections,
            CronTrigger(day_of_week="mon-fri", hour=h, minute=m, timezone=IST),
        )
    h, m = (int(x) for x in trk["scorecard"]["time"].split(":"))
    _job(
        sched,
        "scorecard",
        update_scores,
        CronTrigger(day_of_week="mon-fri", hour=h, minute=m, timezone=IST),
    )
    for hhmm in load_yaml("options.yaml")["polling"]["universe_times"]:
        h, m = (int(x) for x in hhmm.split(":"))
        _job(
            sched,
            f"options_universe_{h:02d}{m:02d}",
            poll_universe_options,
            CronTrigger(day_of_week="mon-fri", hour=h, minute=m, timezone=IST),
        )
    # History (Phase 4): one job at a time holds the DuckDB writer lock
    _job(
        sched,
        "history_nightly",
        history_jobs.nightly,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=45, timezone=IST),
    )
    _job(
        sched,
        "history_weekly",
        history_jobs.weekly,
        CronTrigger(day_of_week="sun", hour=6, minute=30, timezone=IST),
    )
    return sched


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    for noisy in ("httpx", "httpcore"):  # their INFO logs print full URLs (bot token!)
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log.info("worker starting")
    build_scheduler().start()


if __name__ == "__main__":
    main()
