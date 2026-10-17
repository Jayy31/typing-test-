"""Dashboard owner (Phase 9): keep `dashboard_owner` equal to DASHBOARD_ALLOWED_EMAIL, so the
read / owner-switch policies admit exactly that one email. Runs at worker start and daily."""

from sqlalchemy import Engine, delete

from app.config import get_settings
from app.db.models import DashboardOwner
from app.db.session import session_scope


def sync_owner(engine: Engine | None = None) -> int:
    email = get_settings().dashboard_allowed_email.strip().lower()
    with session_scope(engine) as s:
        s.execute(delete(DashboardOwner))
        if email:
            s.add(DashboardOwner(email=email))
    return 1 if email else 0
