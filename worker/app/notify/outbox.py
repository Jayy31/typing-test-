"""Alert outbox: enqueue is idempotent (dedupe_key); a job delivers pending rows."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Alert


def enqueue_alert(
    s: Session, kind: str, text: str, dedupe_key: str, signal_id: int | None = None
) -> bool:
    """Queue a Telegram message. Returns False if this dedupe_key was already queued."""
    if s.scalar(select(Alert.id).where(Alert.dedupe_key == dedupe_key)):
        return False
    s.add(Alert(kind=kind, dedupe_key=dedupe_key, text=text, signal_id=signal_id))
    s.flush()
    return True
