from datetime import datetime

from app.config import IST


def as_ist(dt: datetime) -> datetime:
    """Treat naive datetimes (SQLite in tests) as IST; leave aware ones alone."""
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt
