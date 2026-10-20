"""Phase 14 PR 3: strategy paper books (EOD incubation, real fills), promotion and
retirement, portfolio overlap, correlation, and LAB signals in the paper engine."""

import copy
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import IST, get_settings
from app.db.models import (
    Instrument,
    LabBook,
    LabSpec,
    LabTrial,
    LabVerdict,
    NewsItem,
    NewsTag,
    PaperTrade,
    Signal,
    Suggestion,
)
from app.db.session import session_scope
from app.lab import books
from app.lab import registry as R
from app.lab.spec import StrategySpec, cfg
from app.trading import lab_signals
from tests import bt_fixture as F
from tests.test_options_stage import FakeSource

NOW = datetime(2026, 10, 20, 23, 10, tzinfo=IST)


def patch_books(monkeypatch, **over: Any) -> None:
    c = copy.deepcopy(cfg())
    c["books"].update(over)
    monkeypatch.setattr(books, "cfg", lambda: c)


def incubating(engine: Engine, name: str = "post_results", **kw: Any) -> int:
    with Session(engine) as s:
        h = R.register(s, name, "known_pattern", "why", "what")
        sp = R.add_spec(
            s,
            h,
            StrategySpec.model_validate(
                {
                    "name": name,
                    "source": "known_pattern",
                    "trigger": {"event_types": ["results"]},
                    **kw,
                }
            ),
        )
        sp.stage = "incubating"
        s.add(
            LabTrial(
                spec_id=sp.id,
                kind="stage0",
                n_trades=80,
                n_events=60,
                expectancy_pct=6.0,
                ci_lo=1.0,
                ci_hi=11.0,
                sharpe=0.3,
            )
        )
        s.commit()
        return sp.id


def test_books_open_for_incubating_specs_and_news_core(engine: Engine) -> None:
    sid = incubating(engine)
    assert books.open_books(engine, NOW) == 2
    assert books.open_books(engine, NOW) == 0
    with Session(engine) as s:
        keys = {b.book for b in s.scalars(select(LabBook))}
        assert keys == {f"lab:{sid}", "news_core"}


def test_eod_incubation_runs_on_the_window_since_the_book_opened(engine: Engine) -> None:
    con, cal = F.build()
    F.add_training_reactions(con, cal)
    F.add_event(con, "e1", cal[300])
    sid = incubating(engine)
    with Session(engine) as s:
        s.add(
            LabBook(
                spec_id=sid,
                book=f"lab:{sid}",
                status="incubating",
                started_at=datetime.combine(cal[290], datetime.min.time(), IST),
            )
        )
        s.commit()
    assert books.eod_update(con, engine, today=cal[-1]) == 1
    with Session(engine) as s:
        t = s.scalars(select(LabTrial).where(LabTrial.kind == "eod")).one()
        trades = t.metrics_json["trades"]
        assert (
            t.label == "eod_incubation"
            and len(trades) == 1
            and trades[0]["mode"] == "eod_incubation"
        )
        assert trades[0]["entry_day"] == cal[300].isoformat() and t.n_trades == 1
        b = s.scalars(select(LabBook)).one()
        assert b.stats_json["eod_incubation"]["n"] == 1


def _eod(s: Session, sid: int, pcts: list[float]) -> None:
    s.add(
        LabTrial(
            spec_id=sid,
            kind="eod",
            label="eod_incubation",
            n_trades=len(pcts),
            metrics_json={
                "trades": [{"pnl_pct": p, "exit_day": "2026-10-01", "symbol": "X"} for p in pcts]
            },
        )
    )


def _real(s: Session, book: str, pcts: list[float]) -> None:
    for p in pcts:
        sig = Signal(
            symbol="TCS", direction="CALL", score=0, status="CONVERTED_TO_TRADE", book=book
        )
        s.add(sig)
        s.flush()
        s.add(
            PaperTrade(
                signal_id=sig.id,
                entry_ts=NOW,
                entry_premium=10,
                sl=5,
                t1=12,
                t2=15,
                t3=20,
                status="CLOSED",
                book=book,
                pnl_pct=p,
                symbol="TCS",
            )
        )


def test_validated_needs_eod_incubation_and_real_fills_then_suggests(
    engine: Engine, monkeypatch
) -> None:
    patch_books(monkeypatch, incubation_min_trades=3, real_fill_min_trades=2, retire_min_trades=50)
    sid = incubating(engine)
    books.open_books(engine, NOW)
    with Session(engine) as s:
        _eod(s, sid, [5.0, 8.0, 2.0])
        s.commit()
    out = books.review_books(engine, NOW)
    assert out["validated"] == 0  # EOD incubation passed, no real fills yet
    with Session(engine) as s:
        assert s.scalars(select(LabVerdict).where(LabVerdict.stage == "incubation")).one().passed
        _real(s, f"lab:{sid}", [4.0, 6.0])
        s.commit()
    out = books.review_books(engine, NOW)
    assert out == {"validated": 1, "retired": 0, "suggested": 1}
    with Session(engine) as s:
        b = s.scalars(select(LabBook).where(LabBook.book == f"lab:{sid}")).one()
        assert b.status == "validated" and s.get(LabSpec, sid).stage == "validated"
        g = s.scalars(select(Suggestion)).one()
        assert g.kind == "strategy" and g.yaml_path == "slots.s1" and g.proposed_value == sid
        assert g.file == "worker/config/promoted_strategies.yaml" and g.status == "pending"
        assert "spec_yaml" in g.evidence_json


def test_retire_below_the_backtest_interval_and_on_decay(engine: Engine, monkeypatch) -> None:
    patch_books(monkeypatch, retire_min_trades=3)
    sid = incubating(engine)
    books.open_books(engine, NOW)
    with Session(engine) as s:
        _eod(s, sid, [-5.0, -8.0, -2.0])
        s.commit()
    assert books.review_books(engine, NOW)["retired"] == 1
    with Session(engine) as s:
        b = s.scalars(select(LabBook).where(LabBook.book == f"lab:{sid}")).one()
        assert b.status == "retired" and "90% interval" in b.retire_reason
    assert books.cusum_alarm([-30.0] * 10, 6.0, 20.0)
    assert not books.cusum_alarm([6.0, 5.0, 7.0] * 5, 6.0, 20.0)


def _row(
    book: str, sym: str, direction: str, day: str, exit_day: str | None, pnl: float
) -> dict[str, Any]:
    return {
        "book": book,
        "symbol": sym,
        "direction": direction,
        "entry_day": day,
        "exit_day": exit_day,
        "pnl_inr": pnl,
        "marks": [(day, 0.0), (exit_day or day, pnl)],
    }


def test_portfolio_counts_overlaps_once_and_blocks_opposites(engine: Engine, monkeypatch) -> None:
    rows = [
        _row("lab:1", "TCS", "bullish", "2026-09-01", "2026-09-05", 1000.0),
        _row("lab:2", "TCS", "bullish", "2026-09-02", "2026-09-06", 900.0),  # same: counted once
        _row("lab:3", "TCS", "bearish", "2026-09-03", "2026-09-07", -500.0),  # opposite: blocked
        _row("lab:2", "INFY", "bullish", "2026-09-10", "2026-09-12", 300.0),
    ]
    sids = [incubating(engine, f"strat{i}") for i in range(3)]
    with Session(engine) as s:
        for i, sid in enumerate(sids):
            s.add(LabBook(spec_id=sid, book=f"lab:{i + 1}", status="incubating"))
            s.add(
                LabTrial(
                    spec_id=sid,
                    kind="eod",
                    metrics_json={"trades": [r for r in rows if r["book"] == f"lab:{i + 1}"]},
                )
            )
        s.commit()
    view = books.portfolio(engine)
    assert view["trades"] == 2 and view["pnl_inr"] == 1300.0
    assert (
        view["attribution"]["lab:2"]["trades"] == 2
        and view["attribution"]["lab:1"]["pnl_inr"] == 1000.0
    )
    assert len(view["blocked"]) == 1 and view["blocked"][0]["book"] == "lab:3"
    corr = view["correlation"]
    assert (
        set(corr) == {"lab:1", "lab:2", "lab:3"}
        and corr["lab:1"]["lab:2"] == corr["lab:2"]["lab:1"]
    )


# ---- real fills (broker data source) ------------------------------------------------------
def _tag(
    engine: Engine, sym: str = "BHARTIARTL", etype: str = "results", direction: str = "bullish"
) -> None:
    with session_scope(engine) as s:
        if s.get(Instrument, sym) is None:
            s.add(Instrument(symbol=sym, name=sym, sector="Telecom", is_fno=True, lot_size=475))
        n = NewsItem(
            url_hash=f"h{sym}{etype}",
            source="nse",
            title="t",
            url="u",
            published_at=datetime.now(IST),
        )
        s.add(n)
        s.flush()
        s.add(
            NewsTag(news_id=n.id, symbol=sym, event_type=etype, direction=direction, sentiment=0.7)
        )


def test_lab_signals_real_fills_with_portfolio_blocking(engine: Engine) -> None:
    sid = incubating(engine, exits={"max_hold_days": 3, "targets_pct": [30, 60]})
    books.open_books(engine, NOW)
    now = datetime.now(IST)
    src = FakeSource()
    with session_scope(engine) as s:
        assert lab_signals.make_signals(s, src, now) == 0  # first run sets the cursor: no backfill
    _tag(engine)
    with session_scope(engine) as s:
        assert lab_signals.make_signals(s, src, now) == 1
    with session_scope(engine) as s:
        sig = s.scalars(select(Signal)).one()
        assert sig.book == f"lab:{sid}" and sig.status == "LAB" and sig.option_type == "CE"
        assert sig.expiry in src.exp and sig.pricing_status == "priced"
        assert abs(sig.strike - 4000) <= 50 and sig.flags_json["lab"]["rules"]["targets_pct"] == [
            30.0,
            60.0,
        ]
        s.add(
            PaperTrade(
                signal_id=sig.id,
                entry_ts=now,
                entry_premium=10,
                sl=5,
                t1=12,
                t2=15,
                t3=20,
                status="OPEN",
                book="main",
                symbol="BHARTIARTL",
                option_type="CE",
                expiry=sig.expiry,
                strike=sig.strike,
            )
        )
    _tag(engine, direction="bearish", etype="results_miss")
    with session_scope(engine) as s:
        assert lab_signals.make_signals(s, src, now) == 0  # opposite of an open trade: blocked
        b = s.scalars(select(LabBook).where(LabBook.book == f"lab:{sid}")).one()
        assert "opposite" in b.stats_json["blocked"][-1]["reason"]
    src.available = False
    with session_scope(engine) as s:
        assert lab_signals.make_signals(s, src, now) == 0


def test_promoted_strategy_trades_in_the_main_book(engine: Engine, monkeypatch) -> None:
    sid = incubating(engine)
    books.open_books(engine, NOW)
    monkeypatch.setattr(lab_signals, "promoted", lambda: {sid})
    now = datetime.now(IST)
    src = FakeSource()
    with session_scope(engine) as s:
        lab_signals.make_signals(s, src, now)
    _tag(engine)
    with session_scope(engine) as s:
        assert lab_signals.make_signals(s, src, now) == 1
        assert s.scalars(select(Signal)).one().book == "main"


def test_paper_engine_trades_lab_signals_with_spec_exits(engine: Engine, monkeypatch) -> None:
    from app.trading import paper_engine as P

    monkeypatch.setenv("MC_PATHS", "2000")
    get_settings.cache_clear()
    sid = incubating(engine, exits={"max_hold_days": 1, "targets_pct": [300]})
    books.open_books(engine, NOW)
    src = FakeSource()
    d = datetime.now(IST).date()
    while d.weekday() >= 5:
        d += timedelta(days=1)
    now = datetime(d.year, d.month, d.day, 11, 0, tzinfo=IST)
    with session_scope(engine) as s:
        lab_signals.make_signals(s, src, now)
    _tag(engine)
    with session_scope(engine) as s:
        lab_signals.make_signals(s, src, now)
        for g in s.scalars(select(Signal)):
            g.created_at = now - timedelta(minutes=5)
    monkeypatch.setattr(P, "market_open", lambda n: True)
    P.trading_cycle(engine, src, now)
    with session_scope(engine) as s:
        tr = s.scalars(select(PaperTrade)).one()
        assert tr.book == f"lab:{sid}" and tr.rules_json["targets_pct"] == [300.0]
    nxt = now + timedelta(days=1 if now.weekday() < 4 else 3)
    P.trading_cycle(engine, src, nxt)
    with session_scope(engine) as s:
        tr = s.scalars(select(PaperTrade)).one()
        assert tr.status == "CLOSED" and tr.exit_reason == "TIME"
    get_settings.cache_clear()
    _ = pytest
