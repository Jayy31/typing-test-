"""Scheduled history jobs. One job holds the DuckDB writer lock at a time (run sequentially)."""

import logging
from datetime import datetime, timedelta

from app.config import IST, get_settings
from app.history import backfill, corp_actions, event_study, iv_backfill, live_sync, news_history
from app.history.backup import backup
from app.history.store import connect

log = logging.getLogger(__name__)


def nightly() -> int:
    """After the close: today's NSE files, live events, GDELT, incremental IV history."""
    n = backfill.daily_job()
    with connect(write=True) as con:
        today = datetime.now(IST).date()
        n += corp_actions.fetch(today - timedelta(days=30), today + timedelta(days=30), con)
        n += live_sync.sync_live_events(con)
        if get_settings().gdelt_enabled:
            try:
                n += news_history.gdelt(2, con)
            except Exception as exc:  # noqa: BLE001 - optional source
                log.warning("gdelt failed: %s", exc)
        n += iv_backfill.run(con)
    return n


def weekly() -> int:
    """Sunday: refresh curated events, recompute the event study, back up (if enabled)."""
    with connect(write=True) as con:
        live_sync.load_curated(con)
        corp_actions.load_manual(con)  # event_study.run re-applies lot overrides (fill_lots)
        reactions, stats = event_study.run(con)
    log.info("event study: %d reactions, %d stats (%s)", reactions, stats, datetime.now(IST))
    return reactions + backup()
