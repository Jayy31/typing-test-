"""Store the daily regime (live) and backfill history (feat.regime).

Live: VIX (latest macro snapshot, else the history store), its percentile and the Nifty 20-day
return from the history store, FII net over the last 10 days from macro snapshots.
History: VIX + Nifty from the history store only (no FII series; label says so).
"""

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app import flags
from app.config import IST
from app.db.models import MacroSnapshot, MarketRegime
from app.db.session import session_scope
from app.regime.classifier import Regime, cfg, classify, series
from app.timeutil import as_ist

log = logging.getLogger(__name__)


def _index_series(name: str, before: date | None = None) -> dict[date, float]:
    try:
        from app.history.store import connect

        with connect() as con:
            rows = con.execute(
                "SELECT date, close FROM hist_index WHERE lower(index_name) = lower(?)"
                + (" AND date < ?" if before else ""),
                [name, before] if before else [name],
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - no history store yet
        log.info("regime: history store unavailable (%s)", type(exc).__name__)
        return {}
    return {d: float(c) for d, c in rows if c is not None}


def _daily_fii(engine: Engine | None, upto: date) -> dict[date, float]:
    """Last FII net value of each day (Rs crore) from macro snapshots."""
    with session_scope(engine) as s:
        rows = s.execute(
            select(MacroSnapshot.ts, MacroSnapshot.fii_net)
            .where(MacroSnapshot.fii_net.is_not(None))
            .order_by(MacroSnapshot.ts)
        ).all()
    out: dict[date, float] = {}
    for ts, v in rows:
        d = as_ist(ts).date()
        if d <= upto and v is not None:
            out[d] = float(v)
    return out


def _upsert(engine: Engine | None, r: Regime, source: str) -> None:
    with session_scope(engine) as s:
        row = s.get(MarketRegime, r.day) or MarketRegime(day=r.day)
        if row.source == "live" and source == "history":
            return  # never overwrite a live day (it had FII) with the history version
        row.vol, row.direction, row.label, row.inputs = r.vol, r.direction, r.label, r.inputs_text
        row.vix, row.vix_pct, row.nifty_ret_pct, row.fii_net_cr = (
            r.vix,
            r.vix_pct,
            r.nifty_ret_pct,
            r.fii_net_cr,
        )
        row.source, row.computed_at = source, datetime.now(IST)
        s.merge(row)


def compute_today(engine: Engine | None = None, now: datetime | None = None) -> Regime:
    now = now or datetime.now(IST)
    today = now.date()
    c = cfg()
    nifty = _index_series(c["nifty_index"], before=today)
    vix_hist = _index_series(c["vix_index"], before=today)
    with session_scope(engine) as s:
        snap = s.scalars(
            select(MacroSnapshot)
            .where(MacroSnapshot.india_vix.is_not(None))
            .order_by(MacroSnapshot.ts.desc())
            .limit(1)
        ).first()
        live_vix = float(snap.india_vix) if snap and snap.india_vix is not None else None
    v_days = sorted(vix_hist)
    vix = live_vix if live_vix is not None else (vix_hist[v_days[-1]] if v_days else None)
    look = int(c["volatility"]["pct_lookback_days"])
    hist = [vix_hist[d] for d in v_days[-look:]]
    n_days, win = sorted(nifty), int(c["direction"]["nifty_window_days"])
    ret = (nifty[n_days[-1]] / nifty[n_days[-1 - win]] - 1) * 100 if len(n_days) > win else None
    fii = _daily_fii(engine, today)
    f_days = sorted(fii)[-int(c["direction"]["fii_window_days"]) :]
    fii_sum = (
        sum(fii[d] for d in f_days)
        if len(f_days) == int(c["direction"]["fii_window_days"])
        else None
    )
    return classify(today, vix, hist, ret, fii_sum)


def daily_job(engine: Engine | None = None, now: datetime | None = None) -> int:
    with session_scope(engine) as s:
        if not flags.active(s, "feat.regime"):
            return 0
    r = compute_today(engine, now)
    _upsert(engine, r, "live")
    return 1


def backfill(engine: Engine | None = None) -> int:
    """History days from VIX + Nifty (FII unknown). Idempotent; live days are kept."""
    c = cfg()
    regs = series(_index_series(c["nifty_index"]), _index_series(c["vix_index"]))
    for r in regs.values():
        _upsert(engine, r, "history")
    return len(regs)


def current_in(s: Session, day: date) -> Regime | None:
    """Stored regime for `day`, else the latest one before it."""
    row = s.scalars(
        select(MarketRegime)
        .where(MarketRegime.day <= day)
        .order_by(MarketRegime.day.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    used = row.inputs.split(";")[0]
    inputs = tuple(x for x in ("VIX", "Nifty", "FII") if x in used)
    return Regime(
        row.day,
        row.vol,
        row.direction,
        row.vix,
        row.vix_pct,
        row.nifty_ret_pct,
        row.fii_net_cr,
        inputs,
    )


def current(engine: Engine | None = None, day: date | None = None) -> Regime | None:
    with session_scope(engine) as s:
        return current_in(s, day or datetime.now(IST).date())


def regime_context(engine: Engine | None = None, day: date | None = None) -> dict[str, Any] | None:
    r = current(engine, day)
    return r.as_dict() | {"text": r.text()} if r else None
