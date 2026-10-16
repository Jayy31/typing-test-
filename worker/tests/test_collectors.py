from datetime import date, datetime

from sqlalchemy import Engine, select

from app.collectors.calendar import load_manual_events
from app.collectors.fno_meta import SYMBOL_MASTER, parse_symbol_master, sync_instruments
from app.collectors.macro import build_snapshot
from app.collectors.nse import (
    parse_announcements,
    parse_event_calendar,
    parse_fii_dii,
    store_events,
)
from app.config import IST
from app.db.models import EventCalendar, NewsItem
from app.db.session import session_scope
from app.processing.pipeline import gate_news
from app.scheduler import build_scheduler


def test_parse_announcements() -> None:
    rows = [
        {
            "symbol": "TCS",
            "desc": "Outcome of Board Meeting",
            "attchmntText": "Results approved",
            "attchmntFile": "https://nsearchives.nseindia.com/a.pdf",
            "an_dt": "12-Oct-2026 16:05:00",
        },
        {"symbol": "", "desc": "x", "attchmntFile": "u"},  # no symbol -> skipped
    ]
    (item,) = parse_announcements(rows)
    assert item.title == "TCS: Outcome of Board Meeting" and item.kind == "official"
    assert item.published_at == datetime(2026, 10, 12, 16, 5, tzinfo=IST)


def test_parse_fii_dii() -> None:
    rows = [
        {"category": "DII", "netValue": "1,234.50"},
        {"category": "FII/FPI *", "netValue": "-2,000.10"},
    ]
    assert parse_fii_dii(rows) == (-2000.10, 1234.50)
    assert parse_fii_dii([{"category": "DII", "netValue": "1"}]) is None


def test_parse_event_calendar_and_store_only_fno(engine: Engine) -> None:
    rows = [
        {"symbol": "TCS", "purpose": "Financial Results", "date": "15-Oct-2026", "bm_desc": "Q2"},
        {"symbol": "SMALLCO", "purpose": "Financial Results", "date": "15-Oct-2026"},
        {"symbol": "INFY", "purpose": "Dividend", "date": "20-Oct-2026"},
        {"symbol": "WIPRO", "purpose": "Results", "date": "bad-date"},
    ]
    events = parse_event_calendar(rows)
    assert [(e["symbol"], e["event_type"]) for e in events] == [
        ("TCS", "results"),
        ("SMALLCO", "results"),
        ("INFY", "dividend"),
    ]
    sync_instruments(
        parse_symbol_master(SYMBOL_MASTER.read_text(encoding="utf-8")),
        {"TCS": 175, "INFY": 400},
        engine,
    )
    assert store_events(events, engine) == 2  # SMALLCO is not F&O
    assert store_events(events, engine) == 0  # idempotent
    with session_scope(engine) as s:
        assert {e.symbol for e in s.scalars(select(EventCalendar))} == {"TCS", "INFY"}


def test_macro_snapshot_handles_missing_tickers() -> None:
    prices = {"BZ=F": (82.0, 80.0), "INR=X": (88.1, 88.0), "INFY": (21.0, 20.0), "WIT": None}
    snap = build_snapshot(prices, datetime(2026, 10, 12, 9, 0, tzinfo=IST))
    assert snap.brent == 82.0 and snap.usdinr == 88.1 and snap.dxy is None
    assert snap.adr_json == {"INFY": {"adr": "INFY", "last": 21.0, "change_pct": 5.0}}


def test_manual_calendar_is_empty_by_default() -> None:
    assert load_manual_events() == []
    assert isinstance(date.today(), date)


def test_gate_news_marks_relevant_and_dropped(engine: Engine) -> None:
    sync_instruments(
        parse_symbol_master(SYMBOL_MASTER.read_text(encoding="utf-8")), {"BHARTIARTL": 475}, engine
    )
    now = datetime.now(IST)
    with session_scope(engine) as s:
        s.add(
            NewsItem(
                url_hash="1",
                source="x",
                title="Bharti Airtel announces tariff hike",
                url="u1",
                published_at=now,
                status="pending",
            )
        )
        s.add(
            NewsItem(
                url_hash="2",
                source="x",
                title="Stocks to watch: Airtel, SBI, TCS",
                url="u2",
                published_at=now,
                status="pending",
            )
        )
        s.add(
            NewsItem(
                url_hash="3",
                source="x",
                title="Cricket team wins the series in style",
                url="u3",
                published_at=now,
                status="pending",
            )
        )
    assert gate_news(engine) == 3
    with session_scope(engine) as s:
        got = {n.url_hash: n.status for n in s.scalars(select(NewsItem))}
    assert got == {"1": "relevant", "2": "dropped", "3": "dropped"}


def test_scheduler_registers_all_jobs() -> None:
    ids = {j.id for j in build_scheduler().get_jobs()}
    assert {
        "news_day",
        "news_night",
        "gate_news",
        "tag_news",
        "macro",
        "fii_dii",
        "event_calendar",
        "nse_announcements",
        "heartbeat",
        "fno_meta",
    } <= ids
