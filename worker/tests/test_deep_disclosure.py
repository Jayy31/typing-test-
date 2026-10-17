"""Owner rule (Phase 7): insider trades, bulk/block deals, shareholding, ratings and regulatory
actions store both the event date and the public disclosure timestamp; the event study, rules
mode and the backtester must ALWAYS use the disclosure timestamp. These tests fail if any of them
reads the event date."""

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.backtest.data import HistoryData
from app.config import IST
from app.deep import history as deep_history
from app.history import event_study
from tests.test_history_integrity import E1, JUMP, hist  # noqa: F401 - fixture

APP = Path(__file__).resolve().parents[1] / "app"
# Every module that turns historical events into research results
RESEARCH = [
    APP / "history" / "event_study.py",
    APP / "history" / "analogues.py",
    *sorted((APP / "backtest").glob("*.py")),
]


def test_research_code_never_reads_the_event_date() -> None:
    offenders = [
        str(p.relative_to(APP))
        for p in RESEARCH
        if re.search(r"\bevent_date\b", p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"research code must use the disclosure timestamp: {offenders}"


def test_structured_event_rows_are_dated_by_disclosure() -> None:
    disclosed = datetime(2026, 10, 14, 19, 2, tzinfo=IST)
    row = deep_history.event_row(
        "deep:insider_trades:x:insider_buy_promoter",
        disclosed,
        "INFY",
        "IT",
        "insider_buy_promoter",
        "deep:insider_trades",
        "t",
    )
    assert row[1] == date(2026, 10, 14) and row[2] == time(19, 2)
    assert row[7] == "bullish"  # direction from the hand-set rule, never the base rate


def test_event_study_and_backtest_use_disclosure_not_event_date(hist) -> None:  # noqa: F811
    con, cal = hist
    con.execute("DELETE FROM hist_events")
    # traded 10 sessions earlier, disclosed after the close on E1: the move is on E1 + 1
    disclosed = datetime.combine(cal[E1], time(16, 10), IST)
    deep_history.write(
        con,
        [
            (
                deep_history.event_row(
                    "deep:bulk_block_deals:r1:deal_buy_promoter",
                    disclosed,
                    "INFY",
                    "IT",
                    "deal_buy_promoter",
                    "deep:bulk_block_deals",
                    "INFY: block deal",
                ),
                cal[E1 - 10],
            )
        ],
    )
    assert con.execute("SELECT date, event_date FROM hist_events").fetchone() == (
        cal[E1],
        cal[E1 - 10],
    )
    event_study.run(con)
    day, ret = con.execute(
        """SELECT reaction_day, abnormal_ret FROM event_reactions WHERE "window" = 0"""
    ).fetchone()
    assert day == cal[E1 + 1] and abs(ret - JUMP * 100) < 0.05
    # backtester (rules mode reads the same events): reaction day from the disclosure
    events = HistoryData(con).events(cal[0], cal[-1] + timedelta(days=1))
    assert [e.day for e in events] == [cal[E1 + 1]]
    # and the stats are keyed by the tx2 subtype (base rates per counterparty)
    assert (
        con.execute(
            "SELECT count(*) FROM analogue_stats WHERE event_type = 'deal_buy_promoter'"
        ).fetchone()[0]
        > 0
    )


def test_live_rows_sync_with_event_date(engine) -> None:
    import duckdb

    from app.db.models import InsiderTrade
    from app.db.session import session_scope
    from app.history.store import init_schema
    from tests.helpers import seed_instruments

    seed_instruments(engine, {"BHARTIARTL": 475})
    now = datetime.now(IST).replace(microsecond=0)
    with session_scope(engine) as s:
        s.add(
            InsiderTrade(
                ref="r1",
                symbol="BHARTIARTL",
                counterparty="promoter",
                side="buy",
                event_date=now.date() - timedelta(days=5),
                disclosed_at=now,
                subtype="insider_buy_promoter",
                material=True,
            )
        )
    con = duckdb.connect()
    init_schema(con)
    assert deep_history.sync_structured(con, engine) == 1
    d, t, ed, et = con.execute(
        "SELECT date, time, event_date, event_type FROM hist_events"
    ).fetchone()
    assert (d, t) == (now.date(), now.time()) and ed == now.date() - timedelta(days=5)
    assert et == "insider_buy_promoter"
