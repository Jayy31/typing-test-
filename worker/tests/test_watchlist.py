"""Phase 12 PR 3: watchlist paper book (feat.watchlist_book)."""

from datetime import date, datetime

import pytest
from sqlalchemy import Engine, select

from app.db.models import Alert, CalibrationRecord, PaperTrade, Signal
from app.db.session import session_scope
from app.market.options import OptionQuoteRow
from app.signals import options_stage
from app.signals.options_stage import EvaluatedCandidate, OptionPlan
from app.sim.simulator import SimResult
from app.trading import watchlist
from app.trading.paper_engine import trading_cycle
from tests.test_paper_trading import FakeSource, weekday_at

GATES = {
    "edge_ratio_min": 1.2,
    "min_p_t1_before_sl": 0.55,
    "min_ev_pct_of_premium": 10.0,
    "min_ev_pct": 0.0,
}
EXP = date(2026, 11, 24)


def _plan(edge: float, p: float, ev: float, rejection=None) -> OptionPlan:
    row = OptionQuoteRow(
        "TCS",
        EXP,
        4000.0,
        "CE",
        49.0,
        50.0,
        49.5,
        30.0,
        100000,
        5000,
        4000.0,
        datetime(2026, 10, 19, 11),
    )
    res = SimResult(ev, ev * 100, p, 0.3, 0.1, -1000, 3, -50, 1.0, 2.0, (60, 70, 100), {})
    return OptionPlan(
        status="rejected" if rejection else "priced",
        edge_ratio=edge,
        candidates=[EvaluatedCandidate(row, None, res, None)],
        rejection=rejection,
    )


@pytest.mark.parametrize(
    "score,edge,p,ev,rej,gate",
    [
        (80, 1.15, 0.60, 15, ("edge", "x"), "edge_ratio"),  # edge within margin
        (80, 1.3, 0.52, 15, ("probability", "x"), "p_t1_before_sl"),
        (80, 1.3, 0.60, 8, ("ev", "x"), "ev_pct"),
        (62, 1.3, 0.60, 15, None, "score"),  # only the score is short (threshold 65, margin 5)
    ],
)
def test_one_soft_gate_within_margin_is_a_near_miss(score, edge, p, ev, rej, gate) -> None:
    miss = watchlist.near_miss(
        score, True, _plan(edge, p, ev, rej), GATES, date(2026, 10, 19), "bullish"
    )
    assert miss is not None and miss.gate == gate


@pytest.mark.parametrize(
    "score,edge,p,ev,rej,eligible",
    [
        (80, 1.05, 0.60, 15, ("edge", "x"), True),  # beyond the margin
        (80, 1.15, 0.52, 15, ("edge", "x"), True),  # two soft gates failed
        (80, 1.15, 0.60, 15, ("liquidity", "x"), True),  # a hard check failed
        (80, 1.15, 0.60, 15, ("edge", "x"), False),  # eligibility (ban list, actions, expiry)
        (80, 1.3, 0.60, 15, None, True),  # passed everything: a main-book signal, not a miss
    ],
)
def test_not_a_near_miss(score, edge, p, ev, rej, eligible) -> None:
    assert (
        watchlist.near_miss(
            score, eligible, _plan(edge, p, ev, rej), GATES, date(2026, 10, 19), "bullish"
        )
        is None
    )


def test_wrong_side_or_index_never_enters() -> None:
    assert (
        watchlist.near_miss(
            80, True, _plan(1.15, 0.6, 15, ("edge", "x")), GATES, date(2026, 10, 19), "bearish"
        )
        is None
    )


def test_watchlist_book_is_traded_apart_from_the_main_book(engine: Engine, monkeypatch) -> None:
    """A near-miss gets a WATCHLIST signal in the watchlist book, is entered by the same paper engine,
    sends no Telegram alert, and stays out of main stats, portfolio and summaries."""
    from app.analytics.live import closed_trades
    from app.notify.briefs import evening_summary_text
    from tests.test_paper_trading import make_signal

    monkeypatch.setattr(
        options_stage, "selection_rejection", lambda r, gates=None: ("probability", "short")
    )

    def fake_near(score, eligible, plan, gates, today, direction):
        return (
            watchlist.GateCheck("p_t1_before_sl", 0.53, 0.55, 0.50)
            if plan and plan.rejection
            else None
        )

    monkeypatch.setattr(watchlist, "near_miss", fake_near)
    src = FakeSource()
    now = weekday_at(11, 0)
    make_signal(engine, src, now)
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()  # no main-book signal: only the watchlist one
        assert (sig.status, sig.book, sig.pricing_status) == ("WATCHLIST", "watchlist", "priced")
        assert sig.flags_json["watchlist"]["gate"] == "p_t1_before_sl"
        rec = s.scalars(
            select(CalibrationRecord).where(CalibrationRecord.target == "p_t1_before_sl")
        ).first()
        assert rec is not None and rec.book == "watchlist" and rec.signal_id == sig.id
        s.execute(Alert.__table__.delete())
    assert trading_cycle(engine, src, now) == 1
    with session_scope(engine) as s:
        tr = s.scalars(select(PaperTrade)).one()
        assert tr.book == "watchlist" and tr.status == "OPEN"
        assert (
            s.scalars(select(Alert).where(Alert.kind == "trade")).all() == []
        )  # no per-trade alert
        tr.status, tr.exit_ts, tr.pnl, tr.pnl_pct = "CLOSED", now, 500.0, 10.0
    with session_scope(engine) as s:
        assert closed_trades(s) == []  # main stats exclude the watchlist
        assert len(closed_trades(s, book="watchlist")) == 1
        text = evening_summary_text(s, now.replace(hour=16)) or ""
    assert "WATCHLIST BOOK" in text and "closed today 1" in text


def test_calibration_switch_and_learning_use_the_watchlist(engine: Engine, monkeypatch) -> None:
    from app.calibration import service as cal

    real = cal.load_yaml

    def no_watch(name):
        data = real(name)
        return (
            {**data, "calibration": {"include_in_fit": False}} if name == "watchlist.yaml" else data
        )

    with session_scope(engine) as s:
        for book in ("main", "watchlist"):
            s.add(
                CalibrationRecord(
                    target="p_t1_before_sl",
                    predicted=0.6,
                    outcome=True,
                    book=book,
                    recorded_at=datetime(2026, 10, 1, 10),
                    resolved_at=datetime(2026, 10, 5),
                )
            )
    monkeypatch.setenv("AI_MODEL_KNOWLEDGE_CUTOFF", "2026-06-30")
    from app.config import get_settings

    get_settings.cache_clear()
    with session_scope(engine) as s:
        assert len(cal.dataset(s, "p_t1_before_sl", "all")) == 2  # default: included in fitting
        monkeypatch.setattr(cal, "load_yaml", no_watch)
        assert len(cal.dataset(s, "p_t1_before_sl", "all")) == 1
        rep = cal.report(s, "p_t1_before_sl")
        assert rep["book:main"]["records"] == 1 and rep["book:watchlist"]["records"] == 1
    get_settings.cache_clear()


def test_learning_counts_watchlist_fills_once(engine: Engine) -> None:
    from app.analytics.learning import opportunities
    from app.config import IST
    from app.db.models import RejectedCandidate

    when = datetime(2026, 10, 10, 12, tzinfo=IST)
    with session_scope(engine) as s:
        g = Signal(
            symbol="TCS",
            direction="CALL",
            score=63,
            status="CONVERTED_TO_TRADE",
            book="watchlist",
            analysis_id=7,
            created_at=when,
            updated_at=when,
        )
        s.add(g)
        s.flush()
        s.add(
            PaperTrade(
                signal_id=g.id,
                book="watchlist",
                status="CLOSED",
                entry_ts=when,
                exit_ts=when,
                entry_premium=10,
                lots=3,
                sl=5,
                t1=12.5,
                t2=15,
                t3=20,
                pnl=300,
                pnl_pct=12.0,
            )
        )
        s.add(
            RejectedCandidate(
                dedupe_key="r1",
                ts=when,
                symbol="TCS",
                stage="score",
                reason="x",
                analysis_id=7,
                option_return_pct=9.0,
                outcome_status="resolved",
            )
        )
    with session_scope(engine) as s:
        opps = opportunities(
            s, datetime(2026, 10, 1, tzinfo=IST), datetime(2026, 11, 1, tzinfo=IST)
        )
    assert [(o.ret, o.taken) for o in opps] == [(12.0, False)]  # real fill, not the counterfactual
