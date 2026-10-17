"""Alert outbox: enqueue is idempotent (dedupe_key); a job delivers pending rows."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Alert


def enqueue_alert(
    s: Session,
    kind: str,
    text: str,
    dedupe_key: str,
    signal_id: int | None = None,
    file_path: str | None = None,
) -> bool:
    """Queue a Telegram message. Returns False if this dedupe_key was already queued."""
    if s.scalar(select(Alert.id).where(Alert.dedupe_key == dedupe_key)):
        return False
    link = deep_link(kind, dedupe_key, signal_id)
    if link:
        text = f"{text}\nOpen: {link}"
    s.add(
        Alert(kind=kind, dedupe_key=dedupe_key, text=text, signal_id=signal_id, file_path=file_path)
    )
    s.flush()
    return True


def deep_link(kind: str, dedupe_key: str, signal_id: int | None) -> str | None:
    """Dashboard page for an alert (Phase 9): the exact trade or signal, else the matching page.
    None until DASHBOARD_BASE_URL is set."""
    base = get_settings().dashboard_base_url.rstrip("/")
    if not base:
        return None
    if dedupe_key.startswith("trade:"):
        return f"{base}/trades/{dedupe_key.split(':')[1]}"
    if signal_id is not None:
        return f"{base}/signals/{signal_id}"
    if kind == "report":
        return f"{base}/reports/{dedupe_key.split(':')[1]}"
    page = {
        "health": "health",
        "digest": "digest",
        "sector": "sectors",
        "brief": "",
        "suggestion": "suggestions",
    }.get(kind)
    return None if page is None else f"{base}/{page}".rstrip("/")
