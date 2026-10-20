"""Scheduled history jobs. One job holds the DuckDB writer lock at a time (run sequentially)."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from app.config import IST, get_settings
from app.history import (
    backfill,
    corp_actions,
    event_study,
    iv_backfill,
    live_sync,
    news_history,
    units,
)
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
        n += _deep_history(con)
        if get_settings().gdelt_enabled:
            try:
                n += news_history.gdelt(2, con)
            except Exception as exc:  # noqa: BLE001 - optional source
                log.warning("gdelt failed: %s", exc)
        n += iv_backfill.run(con)
        problems = units.run_detection(con)
    if problems:  # inconclusive unit detection: tell the owner once a week, keep the config default
        from app.db.session import session_scope
        from app.notify.outbox import enqueue_alert

        with session_scope() as s:
            text = (
                "HISTORY CHECK\nCould not tell whether bhavcopy volume/OI are in contracts or shares:\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\nCheck worker/config/backtest.yaml `units`."
            )
            week = datetime.now(IST).strftime("%G-W%V")
            enqueue_alert(s, "health", text, f"units:{week}")
    return n


def _deep_history(con: Any) -> int:
    """Phase 7: materiality stats, live structured events, next chunk of the 5-year backfill."""
    from app.deep import history as deep_history
    from app.deep import stats as deep_stats
    from app.kg import graph as kg_graph
    from app.kg import history as kg_history

    n = 0
    steps: list[Callable[[Any], int]] = [
        lambda _con: kg_graph.build_static(),  # sub-sector competitors, seed, promoter groups
        kg_history.sync_kg_edges,
        deep_stats.refresh,
        deep_history.sync_structured,
        deep_history.backfill_structured,
        _scheduled,  # Phase 14: board-meeting intimations -> hist_scheduled
    ]
    for step in steps:
        try:
            n += step(con)
        except Exception as exc:  # noqa: BLE001 - one step must not stop the nightly job
            log.warning("deep history step failed: %s", exc)
    return n


def _scheduled(con: Any) -> int:
    from app.lab.scheduled import build

    return build(con)


def weekly() -> int:
    """Sunday: refresh curated events, recompute the event study, back up (if enabled)."""
    with connect(write=True) as con:
        live_sync.load_curated(con)
        corp_actions.load_manual(con)  # event_study.run re-applies lot overrides (fill_lots)
        reactions, stats = event_study.run(con)
        from app.history.library import sync_library

        sync_library(con)  # dashboard Event Library (Phase 9)
    log.info("event study: %d reactions, %d stats (%s)", reactions, stats, datetime.now(IST))
    return reactions + backup()
