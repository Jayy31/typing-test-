"""Core rules (CLAUDE.md "Hard rules"): the only instruments this system may ever trade.

- NSE F&O stock options only: never an index (Nifty, Sensex, ...) or any index derivative.
- Long options only: one leg, bought; a call for bullish, a put for bearish. No selling, spreads,
  credit strategies or short legs.
- Monthly expiries only: the current month's, or the next month's when the current one is too
  close or the event falls after it; never later than next month. No weekly expiries.

`violation()` returns None when a trade is allowed, else the reason. It is called at signal
creation (options stage), paper-trade entry and backtest entry; tests also check every stored
signal and trade. Values in config/core_rules.yaml.
"""

import calendar
from collections.abc import Iterable
from datetime import date
from typing import Any

from app.yaml_config import load_yaml

STAGE = "core_rule"


class CoreRuleViolation(RuntimeError):
    """A trade that breaks the core rules reached the paper engine."""


def _cfg() -> dict[str, Any]:
    return load_yaml("core_rules.yaml")


def is_index(symbol: str) -> bool:
    s = symbol.upper().strip()
    return s in {x.upper() for x in _cfg()["index_symbols"]} or "NIFTY" in s or "SENSEX" in s


def looks_monthly(expiry: date) -> bool:
    """Without a contract list: monthly stock expiries fall in the last days of the month."""
    last = calendar.monthrange(expiry.year, expiry.month)[1]
    return expiry.day > last - int(_cfg()["monthly_window_days"])


def monthly_expiries(known: Iterable[date]) -> list[date]:
    """The monthly expiry of each month = the last listed expiry in that month."""
    by_month: dict[tuple[int, int], date] = {}
    for d in known:
        k = (d.year, d.month)
        by_month[k] = max(by_month.get(k, d), d)
    return sorted(by_month.values())


def expiry_violation(expiry: date, today: date, known: Iterable[date] | None = None) -> str | None:
    if expiry < today:
        return f"expiry {expiry} is in the past"
    ahead = int(_cfg()["max_months_ahead"])
    if known is not None:
        known = list(known)
        monthly = [d for d in monthly_expiries(known) if d >= today]
        if expiry not in monthly:
            return f"expiry {expiry} is not a monthly expiry"
        if expiry not in monthly[: ahead + 1]:
            return f"expiry {expiry} is later than next month's expiry"
        return None
    if not looks_monthly(expiry):
        return f"expiry {expiry} is not a monthly expiry"
    months = (expiry.year - today.year) * 12 + expiry.month - today.month
    # after this month's expiry the "current" contract is next month's, so allow one more month
    limit = ahead + (1 if looks_monthly(today) else 0)
    if months > limit:
        return f"expiry {expiry} is later than next month's expiry"
    return None


def violation(
    symbol: str,
    direction: str | None,
    option_type: str | None,
    expiry: date | None,
    today: date,
    *,
    side: str = "buy",
    legs: int = 1,
    known_expiries: Iterable[date] | None = None,
    fno_symbols: set[str] | None = None,
) -> str | None:
    if is_index(symbol):
        return f"{symbol} is an index: index derivatives are never traded"
    if fno_symbols is not None and symbol not in fno_symbols:
        return f"{symbol} is not an NSE F&O stock"
    if legs != 1:
        return f"{legs} legs: only single-leg trades are allowed"
    if side != "buy":
        return "only bought (long) options are allowed: no selling or short legs"
    # signals store the option side (CALL/PUT); candidates and trades use bullish/bearish
    want = {"bullish": "CE", "call": "CE", "bearish": "PE", "put": "PE"}.get(
        (direction or "").lower()
    )
    if want is None:
        return f"direction {direction!r} is neither bullish nor bearish"
    if option_type != want:
        return f"{direction} needs a long {want}, not {option_type}"
    if expiry is None:
        return "no expiry"
    return expiry_violation(expiry, today, known_expiries)
