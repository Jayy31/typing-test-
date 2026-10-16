"""Eligibility filters (hard gates) using NSE reference data. Live liquidity comes in Phase 3."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.config_store import get_config
from app.db.models import EventCalendar, Instrument
from app.signals.expiry import trading_days_between
from app.yaml_config import load_yaml


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def load_holidays(s: Session) -> set[date]:
    etype = load_yaml("signals.yaml")["expiry"]["holiday_event_type"]
    return set(s.scalars(select(EventCalendar.event_date).where(EventCalendar.event_type == etype)))


def banned_symbols(s: Session) -> set[str]:
    return set((get_config(s, "fno_ban", {}) or {}).get("symbols", []))


def check_eligibility(
    s: Session, symbol: str, window_days: int, expiry: date, today: date, holidays: set[date]
) -> list[Check]:
    cfg = load_yaml("signals.yaml")
    inst = s.get(Instrument, symbol)
    checks = [
        Check(
            "fno_segment",
            bool(inst and inst.is_fno and inst.lot_size),
            "in NSE F&O" if inst and inst.is_fno else "not in NSE F&O lot-size file",
        ),
        Check(
            "not_in_ban",
            symbol not in banned_symbols(s),
            "in F&O ban list"
            if symbol in banned_symbols(s)
            else ("ok" if get_config(s, "fno_ban") else "ban list never loaded (unchecked)"),
        ),
    ]
    blocked = set(cfg["eligibility"]["blocked_corporate_actions"])
    actions = [
        f"{e.event_type} on {e.event_date}"
        for e in s.scalars(select(EventCalendar).where(EventCalendar.symbol == symbol))
        if e.event_type in blocked and today <= e.event_date <= expiry
    ]
    checks.append(
        Check(
            "no_contract_adjusting_action", not actions, ", ".join(actions) or "none before expiry"
        )
    )
    exp = cfg["expiry"]
    available = trading_days_between(today, expiry, holidays) - int(exp["time_stop_days"])
    required = min(window_days, int(exp["max_required_holding_days"]))
    checks.append(
        Check(
            "enough_days_to_expiry",
            available >= required,
            f"{available} holding days before time stop, need {required}",
        )
    )
    checks.append(Check("option_liquidity", True, "pending live option data (Phase 3)"))
    return checks
