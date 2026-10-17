"""Automatic backtests on the worker (no manual VM steps):

- rules mode `compare`: once, as soon as the history is complete (price, option and index files
  for most of the configured years, the event study and the IV history);
- replay mode `compare`: once at least `replay_min_signals` stored live predictions made after
  AI_MODEL_KNOWLEDGE_CUTOFF exist, then every `replay_every_days`.
Each run sends its Telegram summary (see __main__.execute). State lives in the config table.
"""

import logging
from datetime import datetime, timedelta

import duckdb
from sqlalchemy import Engine

from app.config import IST
from app.db.config_store import get_config, set_config
from app.db.session import session_scope
from app.history.store import connect
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
RULES_KEY, REPLAY_KEY = "backtest.auto.rules_done", "backtest.auto.replay_last"


def history_ready(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    years = float(load_yaml("history.yaml")["years"])
    need_days = int(years * 250 * float(load_yaml("backtest.yaml")["auto"]["min_history_share"]))
    for kind in ("prices", "options", "index"):
        n = con.execute(
            "SELECT count(*) FROM loaded_files WHERE kind = ? AND status = 'ok'", [kind]
        ).fetchone()[0]  # type: ignore[index]
        if n < need_days:
            return False, f"{kind}: {n} of {need_days} trading days loaded"
    for table, what in (
        ("event_reactions", "event study"),
        ("analogue_stats", "event study stats"),
        ("hist_atm_iv", "IV backfill"),
    ):
        if not con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]:  # type: ignore[index]
            return False, f"{what} not done yet"
    return True, "ready"


def auto_backtests(engine: Engine | None = None, now: datetime | None = None) -> str:
    from app.backtest.__main__ import execute, standard_variants
    from app.backtest.data import HistoryData
    from app.backtest.runner import replay_signals

    now = now or datetime.now(IST)
    cfg = load_yaml("backtest.yaml")["auto"]
    years = int(load_yaml("history.yaml")["years"])
    start, end = now.date().replace(year=now.year - years), now.date()
    done = []
    with session_scope(engine) as s:
        rules_done, replay_last = get_config(s, RULES_KEY), get_config(s, REPLAY_KEY)
    with connect() as con:
        ready, why = history_ready(con)
        n_replay = len(replay_signals(engine, HistoryData(con), start, end)[0]) if ready else 0
    if not rules_done:
        if ready:
            execute(standard_variants("rules"), start, end)
            with session_scope(engine) as s:
                set_config(s, RULES_KEY, now.date().isoformat())
            done.append("rules compare")
        else:
            log.info("auto backtest waiting: %s", why)
    due = replay_last is None or now.date() - datetime.fromisoformat(
        str(replay_last)
    ).date() >= timedelta(days=int(cfg["replay_every_days"]))
    if ready and n_replay >= int(cfg["replay_min_signals"]) and due:
        execute(standard_variants("replay"), start, end)
        with session_scope(engine) as s:
            set_config(s, REPLAY_KEY, now.date().isoformat())
        done.append(f"replay compare ({n_replay} signals)")
    return ", ".join(done) or f"nothing due ({why}; {n_replay} post-cutoff live signals)"
