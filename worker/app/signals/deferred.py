"""Signals made after the close or on a holiday (owner rule, Phase 7 results fast-path).

The signal cycle still runs at once. Options cannot be priced from stale quotes, so the signal is
stored with pricing 'pending' and `enter_after` = the next session's open; at the open the stored
prediction is re-priced with live quotes (same options stage and gates), and the paper engine
enters after the usual first-15-minutes rule. Entry TTL counts from that point, not from creation.
"""

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.schemas import Scenario
from app.config import IST
from app.db.models import Analysis, Instrument, Signal
from app.signals.expiry import is_trading_day
from app.signals.lifecycle import LIVE, _version
from app.signals.rejections import record_rejection
from app.timeutil import as_ist
from app.yaml_config import load_yaml


def _hours() -> tuple[time, time]:
    h = load_yaml("options.yaml")["market_hours"]
    return time.fromisoformat(h["open"]), time.fromisoformat(h["close"])


def off_hours(now: datetime, holidays: set[date]) -> bool:
    op, cl = _hours()
    t = now.time().replace(tzinfo=None)
    return not (is_trading_day(now.date(), holidays) and op <= t <= cl)


def next_open(now: datetime, holidays: set[date]) -> datetime:
    """Open of the next session (today's if before the open on a trading day)."""
    op, _ = _hours()
    d = now.date()
    if not (is_trading_day(d, holidays) and now.time().replace(tzinfo=None) < op):
        d += timedelta(days=1)
        while not is_trading_day(d, holidays):
            d += timedelta(days=1)
    return datetime.combine(d, op, IST)


def entry_start(sig: Signal) -> datetime:
    """When the entry TTL starts: creation, or for a deferred signal its next-open entry time."""
    created = as_ist(sig.created_at)
    if sig.enter_after is None:
        return created
    mins = int(load_yaml("entry.yaml")["no_entry_first_minutes"])
    return max(created, as_ist(sig.enter_after) + timedelta(minutes=mins))


def deferred_json(
    item: Any, out: Any, dist: list[Scenario], move_pct: float, p_move: float | None
) -> dict[str, Any]:
    """What re-pricing at the open needs (stored on the analysis)."""
    return {
        "direction": out.direction,
        "dist": [sc.model_dump(mode="json") for sc in dist],
        "move_pct": move_pct,
        "p_move": p_move,
        "window_days": out.window_days,
        "news_time": min(i.published_at for i in item.cand.items).isoformat(),
        "source": item.source,
    }


def due(s: Session, now: datetime) -> list[Signal]:
    return [
        g
        for g in s.scalars(
            select(Signal).where(
                Signal.status.in_(LIVE),
                Signal.pricing_status == "pending",
                Signal.enter_after.is_not(None),
            )
        )
        if g.enter_after is not None and as_ist(g.enter_after) <= now
    ]


def reprice_due(engine: Any, source: Any, now: datetime) -> int:
    """Re-price deferred signals at the open. Rejections cancel the signal (recorded for the
    counterfactual tracker). Returns the number of signals priced."""
    from app.calibration import service as calibration
    from app.db.session import session_scope
    from app.signals.options_stage import plan_options
    from app.signals.service import _plan_json, apply_plan

    priced = 0
    with session_scope(engine) as s:
        todo = [(g.id, g.analysis_id) for g in due(s, now)]
    for sig_id, analysis_id in todo:
        with session_scope(engine) as s:
            sig = s.get(Signal, sig_id)
            an = s.get(Analysis, analysis_id) if analysis_id else None
            d = ((an.scores_json or {}).get("deferred_pricing") if an else None) or None
            if sig is None or an is None or d is None:
                continue
            policy = calibration.policy(s, d["source"], d["p_move"])
            inst = s.get(Instrument, sig.symbol)
            lot = int(inst.lot_size or 0) if inst else 0
        plan = plan_options(
            source,
            sig.symbol,
            d["direction"],
            [Scenario.model_validate(x) for x in d["dist"]],
            float(d["move_pct"]),
            int(d["window_days"]),
            datetime.fromisoformat(d["news_time"]),
            lot,
            now,
            engine,
            policy,
        )
        with session_scope(engine) as s:
            sig = s.get(Signal, sig_id)
            an = s.get(Analysis, analysis_id)
            assert sig is not None and an is not None
            an.scores_json = {**(an.scores_json or {}), "options": _plan_json(plan)}
            apply_plan(s, sig, plan, now)
            if plan.chosen is None:
                stage, reason = plan.rejection or ("options", f"pricing at the open: {plan.status}")
                sig.status, sig.updated_at = "CANCELLED", now
                _version(s, sig, f"re-priced at the open and rejected: {reason}", now)
                ref = plan.best_reference
                record_rejection(
                    s,
                    dedupe_key=f"deferred:{sig.id}",
                    ts=now,
                    symbol=sig.symbol,
                    stage=f"options_{stage}",
                    reason=reason,
                    direction=sig.direction,
                    signal_id=sig.id,
                    analysis_id=an.id,
                    spot_at_reject=plan.spot,
                    predicted_move_pct=sig.predicted_move_pct,
                    window_days=int(d["window_days"]),
                    ref_expiry=ref.row.expiry if ref else None,
                    ref_strike=ref.row.strike if ref else None,
                    ref_option_type=ref.row.option_type if ref else None,
                    ref_premium=ref.row.ask if ref else None,
                )
            else:
                priced += 1
    return priced
