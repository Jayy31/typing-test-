from datetime import date, datetime

from sqlalchemy import Engine

from app.config import IST
from app.db.models import MacroSnapshot
from app.db.session import session_scope
from app.rules.engine import (
    RuleContext,
    RuleHit,
    adjustment_and_alignment,
    evaluate,
    fired_macro_rules,
)
from app.rules.macro_changes import compute_changes
from app.signals.expiry import choose_expiry, monthly_expiry, trading_days_between


def test_macro_rule_fires_only_past_threshold() -> None:
    assert fired_macro_rules({"brent": 1.0}) == []
    fired = fired_macro_rules({"brent": 4.2, "usdinr": -0.8})
    assert {r["name"] for r in fired} == {"crude_up", "rupee_stronger"}


def test_macro_effects_target_sector_and_subsector() -> None:
    fired = fired_macro_rules({"brent": 4.0})
    omc = RuleContext("BPCL", "Oil & Gas", "OMC", "", frozenset(), frozenset())
    upstream = RuleContext("ONGC", "Oil & Gas", "Upstream", "", frozenset(), frozenset())
    it = RuleContext("TCS", "IT", "IT Services", "", frozenset(), frozenset())
    assert [(h.direction, h.weight) for h in evaluate(omc, fired)] == [("bearish", 8.0)]
    assert [(h.direction, h.weight) for h in evaluate(upstream, fired)] == [("bullish", 8.0)]
    assert evaluate(it, fired) == []


def test_playbook_needs_keyword_and_theme() -> None:
    text = "RBI cuts repo rate by 25 bps"
    bank = RuleContext("SBIN", "Banks", "PSU Bank", text, frozenset({"rates"}), frozenset())
    hits = evaluate(bank, [])
    assert [(h.name, h.direction) for h in hits] == [("rbi_rate_cut", "bullish")]
    no_theme = RuleContext("SBIN", "Banks", "PSU Bank", text, frozenset(), frozenset())
    assert evaluate(no_theme, []) == []
    it = RuleContext("TCS", "IT", "IT Services", text, frozenset({"rates"}), frozenset())
    assert evaluate(it, []) == []


def test_adjustment_agree_disagree_and_cap() -> None:
    hits = [RuleHit("a", "macro", "bullish", 8, ""), RuleHit("b", "playbook", "bearish", 3, "")]
    adj, align = adjustment_and_alignment(hits, "bullish", cap=15)
    assert adj == 5 and round(align, 1) == 72.7
    adj, _ = adjustment_and_alignment(hits, "bearish", cap=15)
    assert adj == -5
    big = [RuleHit("x", "macro", "bullish", 30, "")]
    assert adjustment_and_alignment(big, "bullish", cap=15)[0] == 15
    assert adjustment_and_alignment([], "bullish", cap=15) == (0.0, 50.0)


def test_compute_changes(engine: Engine) -> None:
    t = datetime(2026, 10, 15, 9, 0, tzinfo=IST)
    with session_scope(engine) as s:
        s.add(MacroSnapshot(ts=datetime(2026, 10, 14, 9, 0), brent=80.0, usdinr=88.0, us10y=4.0))
        s.add(MacroSnapshot(ts=t.replace(tzinfo=None), brent=84.0, usdinr=88.0, us10y=4.15))
    with session_scope(engine) as s:
        ch = compute_changes(s, t)
    assert round(ch["brent"], 2) == 5.0 and ch["usdinr"] == 0.0 and round(ch["us10y"], 2) == 0.15


def test_monthly_expiry_last_tuesday_and_holiday_rollback() -> None:
    assert monthly_expiry(2026, 10, 1, set()) == date(2026, 10, 27)
    assert monthly_expiry(2026, 11, 1, set()) == date(2026, 11, 24)
    assert monthly_expiry(2026, 12, 1, set()) == date(2026, 12, 29)
    assert monthly_expiry(2026, 10, 1, {date(2026, 10, 27)}) == date(2026, 10, 26)


def test_trading_days_and_expiry_choice() -> None:
    assert trading_days_between(date(2026, 10, 20), date(2026, 10, 27), set()) == 5
    assert trading_days_between(date(2026, 10, 20), date(2026, 10, 27), {date(2026, 10, 22)}) == 4
    # plenty of days left -> current month
    assert choose_expiry(date(2026, 10, 5), 1, 8, set()) == date(2026, 10, 27)
    # too close -> next month
    assert choose_expiry(date(2026, 10, 20), 1, 8, set()) == date(2026, 11, 24)
    # after this month's expiry -> next month
    assert choose_expiry(date(2026, 10, 28), 1, 8, set()) == date(2026, 11, 24)
    # December rolls the year
    assert choose_expiry(date(2026, 12, 30), 1, 8, set()) == date(2027, 1, 26)
