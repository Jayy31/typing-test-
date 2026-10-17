"""Phase 9: morning brief, evening summary, owner switches, deep links."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import Engine, select

from app.config import IST, get_settings
from app.db.config_store import set_config
from app.db.models import (
    Alert,
    EventCalendar,
    MacroSnapshot,
    PaperTrade,
    RejectedCandidate,
    ResultsExtract,
    Signal,
)
from app.db.session import session_scope
from app.notify import briefs
from app.notify.outbox import deep_link, enqueue_alert
from tests.helpers import seed_instruments

THU = datetime(2026, 10, 15, 8, 45, tzinfo=IST)  # Thursday
SAT = datetime(2026, 10, 17, 8, 45, tzinfo=IST)


def _seed(engine: Engine) -> None:
    seed_instruments(engine, {"TCS": 175, "INFY": 400})
    with session_scope(engine) as s:
        s.add(
            MacroSnapshot(
                ts=datetime(2026, 10, 14, 18, 0, tzinfo=IST), fii_net=-1234.5, dii_net=987.0
            )
        )
        s.add(
            MacroSnapshot(
                ts=datetime(2026, 10, 15, 8, 30, tzinfo=IST),
                brent=80.1,
                usdinr=84.2,
                india_vix=13.5,
                commodities_json={"gold": 2400.0},
            )
        )
        s.add(
            EventCalendar(symbol="TCS", event_type="results", event_date=THU.date(), source="nse")
        )
        s.add(
            Signal(
                symbol="INFY",
                direction="bullish",
                score=72,
                status="NEW",
                enter_after=datetime(2026, 10, 15, 9, 15, tzinfo=IST),
            )
        )
        s.add(
            RejectedCandidate(
                dedupe_key="r1",
                ts=THU - timedelta(hours=3),
                symbol="TCS",
                stage="options_edge",
                reason="edge ratio 0.9 below 1.2",
                direction="bearish",
                score=60,
            )
        )
        s.add(
            RejectedCandidate(
                dedupe_key="r2",
                ts=THU - timedelta(hours=2),
                symbol="INFY",
                stage="candidate",
                reason="weak",
                direction="bullish",
                score=10,
            )
        )


def test_morning_brief_contents(engine: Engine) -> None:
    _seed(engine)
    with session_scope(engine) as s:
        text = briefs.morning_brief_text(s, THU) or ""
    assert text.startswith("MORNING BRIEF  Thu 15-Oct-2026")
    assert "FII/DII (14-Oct, final)" in text and "FII Rs -1,234 cr" in text  # previous session
    assert "Results: TCS" in text and "Brent 80.10" in text and "gold" in text
    assert "S-1 INFY bullish score 72 - enters after the open" in text
    top = text.split("TOP REJECTED")[1]
    assert top.index("TCS") < top.index("INFY")  # strongest rejected first (B8)


def test_morning_brief_skips_non_trading_days_unless_overnight_results(engine: Engine) -> None:
    _seed(engine)
    with session_scope(engine) as s:
        assert briefs.morning_brief_text(s, SAT) is None
        s.add(
            ResultsExtract(
                symbol="TCS",
                period_end=date(2026, 9, 30),
                method="xbrl",
                disclosed_at=datetime(2026, 10, 16, 18, 0, tzinfo=IST),
                subtype="results_beat",
                surprise_score=0.6,
            )
        )
    with session_scope(engine) as s:
        text = briefs.morning_brief_text(s, SAT) or ""
        assert (
            "RESULTS SINCE THE LAST CLOSE" in text and "TCS: results_beat (surprise +0.60)" in text
        )


def test_evening_summary_pnl_and_provisional_flows(engine: Engine) -> None:
    _seed(engine)
    now = datetime(2026, 10, 15, 16, 0, tzinfo=IST)
    with session_scope(engine) as s:
        s.add(
            PaperTrade(
                signal_id=1,
                entry_ts=now - timedelta(hours=5),
                entry_premium=50,
                lots=3,
                sl=25,
                t1=62.5,
                t2=75,
                t3=100,
                status="OPEN",
                symbol="INFY",
                option_type="CE",
                strike=1500,
                lot_size=400,
                last_premium=60,
                state_json={"fills": [{"fraction": 1 / 3, "price": 62.5}]},
            )
        )
    with session_scope(engine) as s:
        text = briefs.evening_summary_text(s, now) or ""
    assert "INFY 1500CE: +20.0% (Rs +8,000)" in text  # 2 of 3 lots still open
    assert "pending (NSE publishes around 19:00" in text
    with session_scope(engine) as s:
        s.add(
            MacroSnapshot(
                ts=datetime(2026, 10, 15, 18, 0, tzinfo=IST), fii_net=100.0, dii_net=-50.0
            )
        )
    with session_scope(engine) as s:
        assert "provisional: FII Rs +100 cr" in (briefs.evening_summary_text(s, now) or "")


def test_switches_and_once_per_day(engine: Engine) -> None:
    _seed(engine)
    assert briefs.morning_brief_job(engine, THU) == 1
    assert briefs.morning_brief_job(engine, THU) == 0  # once per day
    with session_scope(engine) as s:
        set_config(s, "brief.evening_enabled", False)  # owner switch from the dashboard
    assert briefs.evening_summary_job(engine, THU.replace(hour=16)) == 0
    with session_scope(engine) as s:
        assert [a.kind for a in s.scalars(select(Alert))] == ["brief"]


def test_deep_links(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    assert deep_link("signal", "x", 5) is None  # no DASHBOARD_BASE_URL yet
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dash.example.com/")
    get_settings.cache_clear()
    try:
        assert deep_link("trade", "trade:42:T1:1", 7) == "https://dash.example.com/trades/42"
        assert deep_link("signal", "sig:new:7", 7) == "https://dash.example.com/signals/7"
        assert deep_link("health", "h", None) == "https://dash.example.com/health"
        assert deep_link("brief", "b", None) == "https://dash.example.com"
        with session_scope(engine) as s:
            enqueue_alert(s, "signal", "NEW SIGNAL", "k1", None)
            enqueue_alert(s, "trade", "PAPER ENTRY", "trade:3:ENTRY", None)
        with session_scope(engine) as s:
            texts = [a.text for a in s.scalars(select(Alert).order_by(Alert.id))]
        assert texts == ["NEW SIGNAL", "PAPER ENTRY\nOpen: https://dash.example.com/trades/3"]
    finally:
        get_settings.cache_clear()
