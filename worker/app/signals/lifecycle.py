"""Signal lifecycle: NEW -> ACTIVE -> UPGRADED/DOWNGRADED -> CANCELLED (or CONVERTED_TO_TRADE).

One live signal per stock. Every material change writes a signal_versions row.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Signal, SignalVersion
from app.timeutil import as_ist
from app.yaml_config import load_yaml

LIVE = ("NEW", "ACTIVE", "UPGRADED", "DOWNGRADED")


@dataclass(frozen=True)
class LifecycleEvent:
    kind: str  # "new" | "upgraded" | "downgraded" | "cancelled"
    signal_id: int
    old_score: float
    reason: str


def live_signal(s: Session, symbol: str) -> Signal | None:
    return s.scalars(
        select(Signal)
        .where(Signal.symbol == symbol, Signal.status.in_(LIVE))
        .order_by(Signal.id.desc())
    ).first()


def _version(s: Session, sig: Signal, reason: str, now: datetime) -> None:
    n = (
        s.scalar(select(func.max(SignalVersion.version)).where(SignalVersion.signal_id == sig.id))
        or 0
    )
    s.add(
        SignalVersion(
            signal_id=sig.id,
            version=n + 1,
            score=sig.score,
            status=sig.status,
            reason=reason,
            ts=now,
        )
    )


def _create(
    s: Session,
    symbol: str,
    sector: str,
    option: str,
    score: float,
    expiry: date,
    analysis_id: int,
    reason: str,
    now: datetime,
) -> Signal:
    sig = Signal(
        symbol=symbol,
        sector=sector,
        direction=option,
        expiry=expiry,
        score=score,
        status="NEW",
        analysis_id=analysis_id,
        created_at=now,
        updated_at=now,
    )
    s.add(sig)
    s.flush()
    _version(s, sig, reason, now)
    return sig


def apply_analysis(
    s: Session,
    *,
    symbol: str,
    sector: str,
    option: str | None,
    score: float,
    eligible: bool,
    threshold: float,
    expiry: date,
    analysis_id: int,
    reason: str,
    now: datetime,
) -> list[LifecycleEvent]:
    """Update signals for one stock from a fresh analysis. option is CALL/PUT or None (neutral)."""
    cfg = load_yaml("signals.yaml")
    wants = option is not None and eligible and score >= threshold
    sig = live_signal(s, symbol)
    events: list[LifecycleEvent] = []

    if sig is None:
        if wants:
            assert option is not None
            new = _create(s, symbol, sector, option, score, expiry, analysis_id, reason, now)
            events.append(LifecycleEvent("new", new.id, score, reason))
        return events

    flipped = option is not None and option != sig.direction
    if flipped or option is None or not eligible or score < float(cfg["cancel_below"]):
        why = (
            "direction reversed on new information"
            if flipped
            else "no longer eligible"
            if not eligible
            else "thesis weakened"
            if option is None
            else f"score fell to {score:.0f}"
        )
        old = sig.score
        sig.status, sig.score, sig.analysis_id, sig.updated_at = (
            "CANCELLED",
            score,
            analysis_id,
            now,
        )
        _version(s, sig, f"{why}: {reason}", now)
        events.append(LifecycleEvent("cancelled", sig.id, old, f"{why}: {reason}"))
        if flipped and wants:
            assert option is not None
            new = _create(s, symbol, sector, option, score, expiry, analysis_id, reason, now)
            events.append(LifecycleEvent("new", new.id, score, reason))
        return events

    delta = score - sig.score
    if abs(delta) >= float(cfg["update_delta"]):
        old = sig.score
        sig.status = "UPGRADED" if delta > 0 else "DOWNGRADED"
        sig.score, sig.analysis_id, sig.updated_at = score, analysis_id, now
        _version(s, sig, reason, now)
        events.append(LifecycleEvent(sig.status.lower(), sig.id, old, reason))
    else:
        if sig.status == "NEW":
            sig.status = "ACTIVE"
            _version(s, sig, "confirmed by re-evaluation", now)
        sig.updated_at = now
    return events


def expire_stale(s: Session, now: datetime) -> int:
    """Quietly cancel live signals past max_age_days (no alert: nothing changed in the thesis)."""
    cutoff = now - timedelta(days=int(load_yaml("signals.yaml")["signals"]["max_age_days"]))
    n = 0
    for sig in s.scalars(select(Signal).where(Signal.status.in_(LIVE))):
        if as_ist(sig.created_at) < cutoff:
            sig.status, sig.updated_at = "CANCELLED", now
            _version(s, sig, "expired: no confirming news within the signal's lifetime", now)
            n += 1
    return n
