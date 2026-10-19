"""Live positioning (feat.positioning).

States come from, in order: a live snapshot (option chain + stock future from the connected broker
data source, when one exists) or the latest end-of-day states from the history store (bhavcopy,
previous session). Stored per stock per day in `positioning_snapshots`.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PositioningSnapshot
from app.positioning.evidence import Evidence, assess
from app.positioning.features import DayInput, OptRow, compute

log = logging.getLogger(__name__)


class FuturesQuoteSource(Protocol):
    """Implemented by a broker data source once connected (data only)."""

    def future(self, symbol: str, expiry: date) -> dict[str, float] | None: ...


def source_label(source: str, as_of: str | date) -> str:
    """How fresh positioning data is. Previous-session closing data must never read as live."""
    if source == "eod":
        return f"EOD, previous session ({as_of})"
    return f"live intraday ({as_of})"


def eod_states(
    symbol: str, day: date, max_age_days: int = 5
) -> tuple[date, dict[str, tuple[str, float]]] | None:
    try:
        from app.history.store import connect

        with connect() as con:
            last = con.execute(
                "SELECT max(date) FROM hist_positioning WHERE symbol = ? AND date < ? AND date >= ?",
                [symbol, day, day - timedelta(days=max_age_days)],
            ).fetchone()
            if not last or last[0] is None:
                return None
            rows = con.execute(
                "SELECT feature, state, value FROM hist_positioning WHERE symbol = ? AND date = ?",
                [symbol, last[0]],
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - no history store / not upgraded
        log.info("positioning: history unavailable (%s)", type(exc).__name__)
        return None
    return last[0], {f: (s, float(v) if v is not None else 0.0) for f, s, v in rows}


def stats_lookup(feature: str, state: str, window: int) -> tuple[int, float, float, float] | None:
    try:
        from app.history.store import connect

        with connect() as con:
            row = con.execute(
                'SELECT n, mean_ret, hit_up, std FROM positioning_stats WHERE feature = ? AND state = ? AND "window" = ?',
                [feature, state, window],
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return (int(row[0]), float(row[1]), float(row[2]), float(row[3])) if row else None


def live_states(
    chain_rows: list[OptRow], spot: float | None, future: dict[str, float] | None
) -> dict[str, tuple[str, float]]:
    """Intraday states from a live chain (+ future when available)."""
    f = future or {}
    return compute(
        DayInput(
            spot=spot,
            fut_close=f.get("last"),
            fut_prev_close=f.get("prev_close"),
            fut_oi=f.get("oi"),
            fut_chg_oi=f.get("chg_oi"),
            near=chain_rows,
        )
    )


def for_signal(
    s: Session, symbol: str, now: datetime, states: dict[str, tuple[str, float]] | None = None
) -> dict[str, Any] | None:
    """Evidence for one stock now: live states if given, else the latest end-of-day ones."""
    source, as_of = "live", now.date()
    if states is None:
        eod = eod_states(symbol, now.date())
        if eod is None:
            return None
        as_of, states = eod
        source = "eod"
    ev = assess(states, stats_lookup)
    row = s.get(PositioningSnapshot, (as_of, symbol)) or PositioningSnapshot(
        day=as_of, symbol=symbol
    )
    row.states_json, row.source, row.computed_at = (
        {k: list(v) for k, v in states.items()},
        source,
        now,
    )
    s.merge(row)
    return {"as_of": as_of.isoformat(), "source": source, "evidence": [e.as_dict() for e in ev]}


def evidence_from(js: dict[str, Any] | None) -> list[Evidence]:
    return [Evidence(**e) for e in (js or {}).get("evidence", [])]


def latest(s: Session, symbol: str) -> PositioningSnapshot | None:
    return s.scalars(
        select(PositioningSnapshot)
        .where(PositioningSnapshot.symbol == symbol)
        .order_by(PositioningSnapshot.day.desc())
        .limit(1)
    ).first()
