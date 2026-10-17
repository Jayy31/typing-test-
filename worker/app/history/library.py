"""Copy event-study statistics to Postgres (`event_library`) for the dashboard (Phase 9).
Market-wide and sector levels for every event type (incl. graph keys kg:<edge>:<type>), regime
'all' and the current regimes, with recent examples; stock-level rows are too many to copy."""

from datetime import datetime
from typing import Any

import duckdb
from sqlalchemy import Engine, delete

from app.config import IST
from app.db.models import EventLibrary
from app.db.session import session_scope
from app.yaml_config import load_yaml


def sync_library(con: duckdb.DuckDBPyConnection, engine: Engine | None = None) -> int:
    n_ex = int(load_yaml("history.yaml")["event_study"]["examples"])
    rows = con.execute(
        """SELECT event_type, sector, regime, "window", n, n_eff, mean_abn_ret, median, hit_rate, p25, p75
           FROM analogue_stats WHERE symbol IS NULL"""
    ).fetchall()
    examples: dict[str, list[dict[str, Any]]] = {}
    for etype, d, sym, direction, abn, title in con.execute(
        """SELECT e.event_type, e.date, r.symbol, e.direction, r.abnormal_ret, e.title
           FROM event_reactions r JOIN hist_events e USING (event_id)
           WHERE r."window" = 3 AND r.weight > 0 AND e.direction IN ('bullish', 'bearish')
           ORDER BY e.date DESC"""
    ).fetchall():
        lst = examples.setdefault(etype, [])
        if len(lst) < n_ex:
            lst.append(
                {
                    "date": str(d),
                    "symbol": sym,
                    "direction": direction,
                    "abnormal_pct": round(float(abn), 2),
                    "title": (title or "")[:120],
                }
            )
    now = datetime.now(IST)
    with session_scope(engine) as s:
        s.execute(delete(EventLibrary))
        for etype, sector, regime, w, n, n_eff, mean, median, hit, p25, p75 in rows:
            s.add(
                EventLibrary(
                    event_type=etype,
                    level_key=f"sector:{sector}" if sector else "all",
                    regime=regime,
                    window=int(w),
                    n=int(n),
                    n_eff=float(n_eff),
                    mean=float(mean),
                    median=float(median),
                    hit_rate=float(hit),
                    p25=float(p25),
                    p75=float(p75),
                    examples_json=examples.get(etype) if not sector else None,
                    computed_at=now,
                )
            )
    return len(rows)
