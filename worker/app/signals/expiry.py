"""Monthly stock-option expiry and trading-day maths. Holidays come from events_calendar.

The expiry computed here is provisional (last <weekday> of the month, moved back over
holidays). Phase 3 replaces it with the real contract list from the broker.
"""

from datetime import date, timedelta


def is_trading_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def monthly_expiry(year: int, month: int, weekday: int, holidays: set[date]) -> date:
    """Last `weekday` of the month; if it is a holiday, the previous trading day."""
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    while not is_trading_day(d, holidays):
        d -= timedelta(days=1)
    return d


def trading_days_between(start: date, end: date, holidays: set[date]) -> int:
    """Trading days in (start, end]: how many sessions remain after `start`."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if is_trading_day(d, holidays):
            n += 1
    return n


def choose_expiry(today: date, weekday: int, min_trading_days: int, holidays: set[date]) -> date:
    """Current month's expiry, or next month's when it has passed or too few sessions remain."""
    current = monthly_expiry(today.year, today.month, weekday, holidays)
    if current >= today and trading_days_between(today, current, holidays) >= min_trading_days:
        return current
    y, m = (today.year + (today.month == 12), today.month % 12 + 1)
    return monthly_expiry(y, m, weekday, holidays)
