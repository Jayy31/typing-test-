"""Counterfactual tracker (v2.1 B1): what rejected candidates would have done.

For each open rejection with a reference option, the would-be trade is replayed with the same
exit rules from the reference ask, using live chains; the stock's move is tracked too.
Rejections without an option reference (e.g. weak evidence) track the stock only.
"""

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, select

from app.config import IST
from app.db.models import RejectedCandidate
from app.db.session import session_scope
from app.market.live import UpstoxOptionData, market_open
from app.market.options import OptionChain
from app.signals.expiry import trading_days_between
from app.signals.filters import load_holidays
from app.timeutil import as_ist
from app.trading import fills
from app.trading.exits import Bar, ExitRules, PositionState, state_from_json, state_to_json, step
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


def _sign(r: RejectedCandidate) -> int:
    if r.ref_option_type:
        return 1 if r.ref_option_type == "CE" else -1
    return 1 if r.direction != "bearish" else -1


def update_one(
    r: RejectedCandidate,
    chain: OptionChain | None,
    spot: float | None,
    now: datetime,
    holidays: set[date],
    rules: ExitRules,
) -> None:
    cfg = load_yaml("tracking.yaml")["counterfactual"]
    d: dict[str, Any] = dict(r.details_json or {})
    cf: dict[str, Any] = dict(d.get("cf") or {})
    sign = _sign(r)
    if spot is not None:
        if r.spot_at_reject is None:
            r.spot_at_reject = spot
            cf["spot_late"] = True  # first spot seen after the rejection, not at it
        r.stock_return_pct = round(sign * (spot / r.spot_at_reject - 1) * 100, 3)
    row = (
        chain.get(r.ref_strike, r.ref_option_type)
        if chain and r.ref_strike and r.ref_option_type
        else None
    )
    if row is not None and row.bid:
        if "state" not in cf:
            entry = r.ref_premium or row.ask
            if not entry:
                return
            if r.ref_premium is None:
                cf["entry_late"] = True
            st = PositionState.open(
                float(entry),
                rules,
                None,
                sign,
                chain.spot if chain else None,
                None,
                r.predicted_move_pct,
                r.window_days,
            )
            cf["entry_fill"] = fills.classify(row.ask_qty, 1)  # lot size unknown here: any size
            cf["entry_ts"] = now.isoformat()
        else:
            st = state_from_json(cf["state"])
        dte = trading_days_between(now.date(), r.ref_expiry, holidays) if r.ref_expiry else 99
        sessions = trading_days_between(as_ist(r.ts).date(), now.date(), holidays)
        st = step(
            st,
            Bar(
                row.bid,
                row.bid,
                row.bid,
                row.bid,
                dte,
                None,
                False,
                chain.spot if chain else None,
                None,
                None,
                sessions,
            ),
            rules,
        )
        cf["state"] = state_to_json(st)
        cf["mark"] = row.bid
        open_part = st.remaining * (row.bid / st.entry - 1) * 100
        r.option_return_pct = round(st.realised_pct() + open_part, 3)
        if st.closed:
            r.exit_reason = st.fills[-1].event
    age = trading_days_between(as_ist(r.ts).date(), now.date(), holidays)
    done = (r.exit_reason is not None) or age >= int(cfg["max_days"])
    if done:
        r.outcome_status = "resolved" if r.ref_strike else "stock_only"
        r.resolved_at = now
        r.exit_reason = r.exit_reason or ("HORIZON" if r.ref_strike else None)
    d["cf"] = cf
    r.details_json = d


def track_rejections(
    engine: Engine | None = None,
    source: UpstoxOptionData | None = None,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(IST)
    source = source or UpstoxOptionData(engine)
    if not source.available or not market_open(now):
        return 0
    cfg = load_yaml("tracking.yaml")["counterfactual"]
    with session_scope(engine) as s:
        rows = s.scalars(
            select(RejectedCandidate)
            .where(RejectedCandidate.outcome_status == "open")
            .order_by(RejectedCandidate.id)
            .limit(int(cfg["max_rows_per_run"]))
        ).all()
        wanted = {(r.symbol, r.ref_expiry) for r in rows if r.ref_expiry and r.ref_strike}
        symbols = {r.symbol for r in rows}
    chains: dict[tuple[str, date], OptionChain] = {}
    for sym, exp in wanted:
        try:
            ch = source.chain(sym, exp)
        except Exception as exc:  # noqa: BLE001
            log.warning("counterfactual chain %s failed: %s", sym, exc)
            continue
        if ch:
            chains[(sym, exp)] = ch
    spots: dict[str, float] = {}
    for sym in symbols:
        try:
            q = source.quote(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("counterfactual quote %s failed: %s", sym, exc)
            continue
        if q:
            spots[sym] = q.last_price
    rules = ExitRules.from_config()
    with session_scope(engine) as s:
        holidays = load_holidays(s)
        n = 0
        for r in s.scalars(
            select(RejectedCandidate).where(RejectedCandidate.id.in_([x.id for x in rows]))
        ):
            chain = chains.get((r.symbol, r.ref_expiry)) if r.ref_expiry else None
            update_one(r, chain, spots.get(r.symbol), now, holidays, rules)
            n += 1
    return n
