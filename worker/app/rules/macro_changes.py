"""Macro move detection: latest snapshot vs the one closest to 24h earlier."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MacroSnapshot


def _pct(new: float | None, old: float | None) -> float | None:
    return None if new is None or not old else (new / old - 1) * 100


def compute_changes(s: Session, now: datetime) -> dict[str, float]:
    """metric -> change. brent/usdinr/dxy in %, us10y as absolute % points. Missing -> omitted."""
    latest = s.scalars(select(MacroSnapshot).order_by(MacroSnapshot.ts.desc()).limit(1)).first()
    if latest is None:
        return {}
    target = latest.ts - timedelta(hours=24)
    older = s.scalars(
        select(MacroSnapshot)
        .where(MacroSnapshot.ts <= target)
        .order_by(MacroSnapshot.ts.desc())
        .limit(1)
    ).first()
    if older is None:
        return {}
    out: dict[str, float] = {}
    for metric in ("brent", "usdinr", "dxy"):
        v = _pct(getattr(latest, metric), getattr(older, metric))
        if v is not None:
            out[metric] = v
    if latest.us10y is not None and older.us10y is not None:
        out["us10y"] = latest.us10y - older.us10y
    return out
