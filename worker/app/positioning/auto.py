"""Positioning history without manual steps (feat.positioning).

Same trigger pattern as the automatic backtests (app/backtest/auto.py): nothing happens until the
5-year history files are loaded. Then, once: stock futures are parsed from the F&O files already in
the download cache, the positioning states and base rates are built, and completion is reported on
Telegram and the dashboard Go-live page. After that the nightly job only adds new days.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from app.config import IST
from app.db.config_store import get_config, set_config
from app.db.models import GoliveCheck
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
DONE_KEY = "positioning.auto.done"
STATUS_KEY = "positioning.auto.status"


def files_ready(con: Any) -> tuple[bool, str]:
    """The history backfill is complete: price, option and index files for most of the years."""
    years = float(load_yaml("history.yaml")["years"])
    need = int(years * 250 * float(load_yaml("backtest.yaml")["auto"]["min_history_share"]))
    for kind in ("prices", "options", "index"):
        n = con.execute(
            "SELECT count(*) FROM loaded_files WHERE kind = ? AND status = 'ok'", [kind]
        ).fetchone()[0]
        if n < need:
            return False, f"{kind}: {n} of {need} trading days loaded"
    return True, "ready"


def first_build(engine: Engine | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Futures from the cache + positioning states + base rates, for the whole history."""
    from app.history import backfill
    from app.history.store import connect
    from app.positioning.history import build, compute_stats

    now = now or datetime.now(IST)
    years = int(load_yaml("history.yaml")["years"])
    start = now.date().replace(year=now.year - years)
    with connect(write=True) as con:
        fut = backfill.run("futures", start, now.date(), con=con, progress=False)
        states = build(con)
        stats = compute_stats(con)
        row = con.execute("SELECT count(DISTINCT date) FROM hist_positioning").fetchone()
        days = row[0] if row else 0
    return {
        "futures_days": fut["ok"],
        "futures_errors": fut["error"],
        "states": states,
        "base_rates": stats,
        "days": int(days or 0),
        "finished_at": now.isoformat(),
    }


def report(engine: Engine | None, res: dict[str, Any], now: datetime) -> None:
    ok = res["futures_errors"] == 0 and res["days"] > 0
    text = (
        "POSITIONING HISTORY READY (automatic)\n"
        f"Futures: {res['futures_days']} trading days parsed from the download cache"
        + (f", {res['futures_errors']} errors" if res["futures_errors"] else "")
        + f"\nPositioning: {res['states']} states over {res['days']} days, "
        f"{res['base_rates']} base rates\n"
        "From now on the nightly job adds each new day."
    )
    with session_scope(engine) as s:
        set_config(s, DONE_KEY, now.date().isoformat())
        set_config(s, STATUS_KEY, res)
        s.add(
            GoliveCheck(
                kind="auto",
                created_at=now,
                status="ok" if ok else "warn",
                text=text,
                results_json={
                    "title": "Positioning history (automatic)",
                    "checks": [
                        {
                            "name": "positioning_history",
                            "status": "ok" if ok else "warn",
                            "summary": f"{res['days']} days, {res['states']} states, "
                            f"{res['base_rates']} base rates",
                            "details": [
                                f"futures: {res['futures_days']} days parsed from the cache, "
                                f"{res['futures_errors']} errors",
                                "nightly job adds each new day from now on",
                            ],
                        }
                    ],
                },
            )
        )
        enqueue_alert(s, "health", text, f"positioning:auto:{now.date()}")


def job(engine: Engine | None = None, now: datetime | None = None) -> str:
    """Nightly: wait for the history, build once, then add new days."""
    from app import flags
    from app.history.store import connect
    from app.positioning.history import nightly

    now = now or datetime.now(IST)
    with session_scope(engine) as s:
        if not flags.active(s, "feat.positioning"):
            return "switched off"
        done = get_config(s, DONE_KEY)
    if done:
        return f"nightly: {nightly()} new states"
    with connect() as con:
        ready, why = files_ready(con)
    if not ready:
        log.info("positioning history waiting: %s", why)
        return f"waiting ({why})"
    res = first_build(engine, now)
    report(engine, res, now)
    return f"first build: {res['days']} days"
