"""CORE RULES (CLAUDE.md "Hard rules"): stock options only, one long leg, CE for bullish and PE
for bearish, monthly expiries no later than next month. Every stored signal, paper trade and
backtest trade is also checked by the autouse guard in tests/conftest.py."""

import re
from datetime import date
from pathlib import Path

import pytest

from app.trading.core_rules import expiry_violation, is_index, monthly_expiries, violation

TODAY = date(2026, 10, 19)
OCT, NOV, DEC = date(2026, 10, 27), date(2026, 11, 24), date(2026, 12, 29)
LISTED = [OCT, NOV, DEC]


def test_allowed_trade() -> None:
    assert violation("TCS", "bullish", "CE", NOV, TODAY, known_expiries=LISTED) is None
    assert violation("TCS", "PUT", "PE", OCT, TODAY) is None


@pytest.mark.parametrize(
    "sym", ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "Nifty 50", "INDIA VIX"]
)
def test_index_never_traded(sym: str) -> None:
    assert is_index(sym)
    assert "index" in (violation(sym, "bullish", "CE", NOV, TODAY) or "")


def test_only_one_bought_leg_matching_direction() -> None:
    assert "short" in (violation("TCS", "bullish", "CE", NOV, TODAY, side="sell") or "")
    assert "legs" in (violation("TCS", "bullish", "CE", NOV, TODAY, legs=2) or "")
    assert "long PE" in (violation("TCS", "bearish", "CE", NOV, TODAY) or "")
    assert "long CE" in (violation("TCS", "bullish", "PE", NOV, TODAY) or "")
    assert violation("TCS", "neutral", "CE", NOV, TODAY)


def test_not_fno_stock() -> None:
    assert "F&O" in (violation("XYZ", "bullish", "CE", NOV, TODAY, fno_symbols={"TCS"}) or "")


def test_monthly_only_and_never_beyond_next_month() -> None:
    weekly = date(2026, 11, 10)
    assert "not a monthly" in (expiry_violation(weekly, TODAY, LISTED + [weekly]) or "")
    assert "not a monthly" in (expiry_violation(weekly, TODAY) or "")
    assert "later than next month" in (expiry_violation(DEC, TODAY, LISTED) or "")
    assert "later than next month" in (expiry_violation(DEC, TODAY) or "")
    assert "past" in (expiry_violation(date(2026, 9, 29), TODAY) or "")
    # after this month's expiry, the current contract is next month's: Dec becomes "next"
    assert expiry_violation(DEC, date(2026, 10, 28), LISTED) is None
    assert monthly_expiries([date(2026, 11, 3), NOV, OCT]) == [OCT, NOV]


def test_options_stage_offers_current_or_next_month_only() -> None:
    """The live pipeline only ever prices the allowed monthly expiries (see conftest guard)."""
    src = Path(__file__).resolve().parents[1] / "app/signals/options_stage.py"
    text = src.read_text()
    assert "core_rules.monthly_expiries" in text and "core_rules.violation" in text


# source scan: no code that sells options, builds spreads or trades an index
FORBIDDEN = [
    re.compile(r"""\b(side|transaction_type|action)\s*[:=]\s*["'](sell|short|write)["']""", re.I),
    re.compile(
        r"\b(credit_spread|debit_spread|bull_spread|bear_spread|iron_condor|iron_fly|"
        r"straddle|strangle|butterfly|short_leg|sell_leg|write_option|naked_short)\b",
        re.I,
    ),
    re.compile(r"""\b(OPTIDX|IDO)\b"""),  # index option instrument codes in NSE files
]


# reading a price is not a position: the ATM straddle price measures the market's implied move
ALLOWED = {("market/implied.py", "straddle")}


def test_no_selling_spreads_or_index_options_in_code() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    hits = [
        f"{p.relative_to(root)}: {m.group(0)}"
        for p in root.rglob("*.py")
        if p.name != "core_rules.py"
        for rx in FORBIDDEN
        for m in rx.finditer(p.read_text(encoding="utf-8"))
        if (str(p.relative_to(root)), m.group(0).lower()) not in ALLOWED
    ]
    assert not hits, f"forbidden trading constructs: {hits}"
