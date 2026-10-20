"""Phase 14 PR 4: /lab, weekly lab summary, digest + monthly sections, strategy alerts."""

from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import IST
from app.db.models import Alert, LabBook
from app.db.session import session_scope
from app.notify import lab
from app.trading import lab_signals
from tests.test_lab_books import _tag, incubating
from tests.test_notify import Recorder, bot
from tests.test_options_stage import FakeSource

NOW = datetime(2026, 10, 25, 9, 5, tzinfo=IST)


def _book(engine: Engine, status: str = "incubating", alerts: bool = False) -> int:
    sid = incubating(engine)
    with Session(engine) as s:
        s.add(
            LabBook(
                spec_id=sid,
                book=f"lab:{sid}",
                status=status,
                alerts_enabled=alerts,
                stats_json={"eod_incubation": {"n": 12, "expectancy_pct": 4.5}},
            )
        )
        s.commit()
    return sid


def test_lab_command_reports_eod_and_real_fills_separately(engine: Engine) -> None:
    _book(engine)
    text = bot(engine, Recorder()).reply_for("/lab")
    assert "STRATEGY LAB" in text and "post_results [incubating]" in text
    assert "EOD incubation 12 trades, +4.5%" in text and "real fills 0" in text
    assert "/lab" in bot(engine, Recorder()).reply_for("/help")


def test_weekly_summary_digest_and_monthly(engine: Engine) -> None:
    _book(engine)
    assert lab.weekly_summary(engine, NOW) == 1
    assert lab.weekly_summary(engine, NOW) == 0  # once a week
    with Session(engine) as s:
        a = s.scalars(select(Alert)).one()
        assert a.text.startswith("STRATEGY LAB WEEK") and "New hypotheses: 1" in a.text
        lines = lab.digest_lines(s, NOW.date())
        assert any("EOD incubation 12 trades" in x for x in lines)
        d = lab.monthly_data(s, NOW.replace(day=1), NOW)
    assert d["hypotheses"] == 1 and "STRATEGY LAB" in lab.monthly_lines(d)


import pytest  # noqa: E402


@pytest.mark.parametrize(
    ("status", "alerts", "expected"),
    [("incubating", True, 0), ("validated", False, 0), ("validated", True, 1)],
)
def test_only_validated_strategies_with_the_switch_on_alert(
    engine: Engine, status, alerts, expected
) -> None:
    now = datetime.now(IST)
    _book(engine, status, alerts)
    src = FakeSource()
    with session_scope(engine) as s:
        lab_signals.make_signals(s, src, now)
    _tag(engine)
    with session_scope(engine) as s:
        assert lab_signals.make_signals(s, src, now) == 1
    with Session(engine) as s:
        alerts_ = s.scalars(select(Alert)).all()
        assert len(alerts_) == expected
        if expected:
            assert alerts_[0].kind == "signal" and alerts_[0].text.startswith("STRATEGY SIGNAL")
