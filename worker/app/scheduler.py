"""All job schedules live here. One worker process, APScheduler inside."""

import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine

from app.ai.tagger import tag_items
from app.calibration import service as calibration
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
from app.health import watchdog
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
from app.notify.digest import weekly_digest
from app.notify.outbox import enqueue_alert
from app.notify.telegram import poll_telegram_job, send_alerts_job
from app.processing.pipeline import gate_news
from app.signals.service import run_signal_cycle
from app.tracking.counterfactual import track_rejections
from app.tracking.postmortem import run_post_mortems
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
        stuck = watchdog.stuck_runs(engine, {})
        if stuck:
            enqueue_alert(
                s,
                "health",
                "STUCK JOBS\n" + "\n".join(f"- {x}" for x in stuck),
                f"stuck:{now:%Y-%m-%d %H}",
            )
        warning = token_expiry_warning(now)
        if warning:
            enqueue_alert(s, "health", warning, f"broker_token:{now.date()}")
    watchdog.ping()  # external heartbeat (healthchecks.io): alerts if the VM itself stops
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


def _deep_jobs(sched: BlockingScheduler) -> None:
    from app.deep import collectors as deep
    from app.deep import extracts, results

    src = load_yaml("deep_data.yaml")["sources"]
    day = {"hour": "7-23", "timezone": IST}
    if src["insider"]["enabled"]:
        _job(sched, "deep_insider", deep.collect_insider, CronTrigger(minute="*/30", **day))
    if src["bulk_deals"]["enabled"]:
        _job(
            sched,
            "deep_bulk_deals",
            deep.collect_bulk_deals,
            CronTrigger(day_of_week="mon-fri", hour="18,20", minute=5, timezone=IST),
        )
    if src["block_deals"]["enabled"]:
        _job(
            sched,
            "deep_block_deals",
            deep.collect_block_deals,
            CronTrigger(day_of_week="mon-fri", hour="18,20", minute=10, timezone=IST),
        )
    if src["shareholding"]["enabled"]:
        _job(
            sched,
            "deep_shareholding",
            deep.collect_shareholding,
            CronTrigger(hour="8,14,20", minute=20, timezone=IST),
        )
    _job(sched, "deep_ratings", deep.scan_ratings, CronTrigger(minute="3-59/10", **day))
    if src["sebi_orders"]["enabled"]:
        _job(sched, "deep_sebi", deep.collect_sebi, CronTrigger(minute=25, **day))
    if src["usfda"]["enabled"]:
        _job(
            sched,
            "deep_usfda",
            deep.collect_usfda,
            CronTrigger(hour="8,11,14,17,20,23", minute=35, timezone=IST),
        )
    if src["financial_results"]["enabled"]:
        fast = int(load_yaml("deep_data.yaml")["fast_path"]["poll_seconds"])
        _job(sched, "results_fast_path", results.fast_path_job, IntervalTrigger(seconds=fast))
        _job(sched, "results_slow_path", results.slow_path_job, CronTrigger(minute="*/15", **day))
    _job(
        sched,
        "deep_auto_sales",
        extracts.scan_auto_sales,
        CronTrigger(day="1-4", hour="8-22", minute="*/30", timezone=IST),
    )
    _job(
        sched,
        "deep_macro_releases",
        extracts.collect_macro_releases,
        CronTrigger(minute=40, hour="8-22", timezone=IST),
    )


def _phase10_jobs(sched: BlockingScheduler) -> None:
    from app.analytics import ai_vs_rules, learning, live, monthly
    from app.health import quality

    # month start, in order: merge last month's accepted change, review + new suggestions, report
    _job(
        sched,
        "learning_merge",
        learning.merge_monthly,
        CronTrigger(day=1, hour=7, minute=50, timezone=IST),
    )
    _job(
        sched,
        "learning_suggest",
        learning.run_monthly,
        CronTrigger(day=1, hour=8, minute=0, timezone=IST),
    )
    _job(
        sched,
        "monthly_report",
        monthly.monthly_report_job,
        CronTrigger(day=1, hour=8, minute=30, timezone=IST),
    )
    _job(sched, "learning_prs", learning.open_prs, IntervalTrigger(minutes=30))
    _job(
        sched,
        "ai_vs_rules",
        ai_vs_rules.resolve,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=40, timezone=IST),
    )
    _job(sched, "drift_check", live.drift_check, CronTrigger(hour=22, minute=50, timezone=IST))
    _job(sched, "data_quality", quality.quality_job, CronTrigger(hour=19, minute=30, timezone=IST))
    _job(
        sched,
        "db_size",
        lambda: len(watchdog.db_size()),
        CronTrigger(hour=3, minute=45, timezone=IST),
    )
    _job(
        sched,
        "ai_cost_report",
        lambda: len(watchdog.cost_report()),
        CronTrigger(hour=3, minute=50, timezone=IST),
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
    _phase10_jobs(sched)
    from app.golive.checks import due_job as golive_due

    _job(sched, "golive_checks", golive_due, IntervalTrigger(minutes=10))
    # Phase 9: Telegram morning brief / evening summary (owner switches brief.*)
    from app.notify.briefs import evening_summary_job, morning_brief_job

    _job(sched, "morning_brief", morning_brief_job, CronTrigger(hour=8, minute=45, timezone=IST))
    from app.regime.service import backfill as regime_backfill
    from app.regime.service import daily_job as regime_daily

    # Phase 12: market regime before the brief, and history backfill after the nightly history load
    _job(sched, "market_regime", regime_daily, CronTrigger(hour=8, minute=35, timezone=IST))
    _job(
        sched,
        "positioning_history",
        positioning_nightly,
        CronTrigger(hour=23, minute=40, timezone=IST),
    )
    _job(
        sched,
        "market_regime_backfill",
        regime_backfill,
        CronTrigger(hour=23, minute=50, timezone=IST),
    )
    _job(
        sched, "evening_summary", evening_summary_job, CronTrigger(hour=16, minute=0, timezone=IST)
    )
    from app.db.owner import sync_owner

    _job(sched, "dashboard_owner", sync_owner, CronTrigger(hour=7, minute=10, timezone=IST))
    # Phase 7: deeper data sources (deep_data.yaml)
    _deep_jobs(sched)
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
    # Phase 5: calibration, post-mortems, weekly digest
    _job(
        sched,
        "calibration_resolve",
        resolve_calibration,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=10, timezone=IST),
    )
    _job(
        sched,
        "calibration_fit",
        fit_calibrators,
        CronTrigger(day_of_week="mon-fri", hour=20, minute=10, timezone=IST),
    )
    _job(sched, "post_mortems", run_post_mortems, IntervalTrigger(minutes=30))
    # Phase 6: automatic backtests (after the history is complete; replay once enough live signals)
    _job(sched, "backtest_auto", backtest_auto, CronTrigger(hour=22, minute=40, timezone=IST))
    # Phase 13 research agent (feat.research_agent): overnight research files + cost check
    _job(sched, "research_files", research_files, CronTrigger(hour=22, minute=10, timezone=IST))
    _job(
        sched,
        "research_files_collect",
        research_files_collect,
        CronTrigger(hour="22-23,0-8", minute="5,35", timezone=IST),
    )
    _job(
        sched,
        "research_review",
        research_review,
        CronTrigger(day_of_week="sun", hour=8, minute=20, timezone=IST),
    )
    _job(sched, "strategy_lab", strategy_lab, CronTrigger(hour=23, minute=10, timezone=IST))
    _job(
        sched,
        "strategy_lab_week",
        strategy_lab_week,
        CronTrigger(day_of_week="sun", hour=9, minute=5, timezone=IST),
    )
    _job(sched, "research_overrun", research_overrun, CronTrigger(hour=21, minute=5, timezone=IST))
    _job(
        sched,
        "weekly_digest",
        weekly_digest,
        CronTrigger(day_of_week="sun", hour=8, minute=50, timezone=IST),
    )
    return sched


def _research_on() -> bool:
    from app import flags
    from app.db.session import session_scope

    with session_scope() as s:
        return flags.active(s, "feat.research_agent")


def research_files() -> int:
    """Phase 13: queue tonight's research files (Batch API)."""
    from app.research.files import nightly_job

    return nightly_job() if _research_on() else 0


def research_files_collect() -> int:
    from app.research.files import collect_job

    return collect_job() if _research_on() else 0


def research_review() -> int:
    """Phase 13: Sunday self-review -> lessons (before the 08:50 digest)."""
    from app.research.review import weekly_review

    return weekly_review() if _research_on() else 0


def strategy_lab() -> int:
    """Phase 14: overnight Strategy Lab loop (waits for the history; flag-gated inside)."""
    from app.lab.nightly import job

    log.info("strategy lab: %s", job())
    return 1


def strategy_lab_week() -> int:
    """Phase 14: weekly Strategy Lab summary on Telegram (feat.strategy_lab)."""
    from app import flags
    from app.db.session import session_scope
    from app.notify.lab import weekly_summary

    with session_scope() as s:
        if not flags.active(s, "feat.strategy_lab"):
            return 0
    return weekly_summary()


def research_overrun() -> int:
    from app.research.runs import overrun_check

    return overrun_check()


def positioning_nightly() -> int:
    """Phase 12: waits for the 5-year history, builds positioning once, then adds new days."""
    from app.positioning.auto import job

    log.info("positioning history: %s", job())
    return 1


def backtest_auto() -> int:
    from app.backtest.auto import auto_backtests

    log.info("auto backtests: %s", auto_backtests())
    return 1


def resolve_calibration() -> int:
    return calibration.resolve()


def fit_calibrators() -> int:
    return len(calibration.fit_all())


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    from app import logsafe

    logsafe.install()  # secrets never reach the logs
    for noisy in ("httpx", "httpcore"):  # their INFO logs print full URLs (bot token!)
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log.info("worker starting")
    from app.db.owner import sync_owner

    run_job("dashboard_owner", sync_owner)  # who may open the dashboard (DASHBOARD_ALLOWED_EMAIL)
    from app.ai.models import startup_check as ai_models_check
    from app.golive.checks import mark_first_start

    run_job("ai_models_check", ai_models_check)  # every role's model in AI_ALLOWED_MODELS
    mark_first_start()  # go-live: quick check after 2 h, first-run report after 24 h (once)
    sched = build_scheduler()
    # Live AI smoke test once, shortly after startup, until it has passed (results to Telegram)
    sched.add_job(
        lambda: run_job("ai_smoke_startup", ai_smoke_startup),
        DateTrigger(run_date=datetime.now(IST) + timedelta(minutes=2)),
        id="ai_smoke_startup",
    )
    sched.start()


def ai_smoke_startup() -> int:
    from app.ai.smoke import startup_check

    outcome = startup_check()
    log.info("AI smoke test at startup: %s", outcome)
    return 1 if outcome in ("passed", "failed") else 0


if __name__ == "__main__":
    main()
