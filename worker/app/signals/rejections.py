"""Rejected candidates (v2.1 B1): every rejection from the candidate stage onward is stored once,
with stage and reason, so the counterfactual tracker (Phase 3b) can see what we missed."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RejectedCandidate


def record_rejection(
    s: Session,
    *,
    dedupe_key: str,
    ts: datetime,
    symbol: str,
    stage: str,
    reason: str,
    **fields: Any,
) -> bool:
    """Idempotent on dedupe_key. Returns False if already recorded."""
    if s.scalar(select(RejectedCandidate.id).where(RejectedCandidate.dedupe_key == dedupe_key)):
        return False
    s.add(
        RejectedCandidate(
            dedupe_key=dedupe_key[:128],
            ts=ts,
            symbol=symbol,
            stage=stage,
            reason=reason[:2000],
            **fields,
        )
    )
    s.flush()
    return True
