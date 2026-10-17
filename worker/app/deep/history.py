"""Structured events in the history store (Phase 7, taxonomy tx2 subtypes).

`hist_events.date` / `time` = PUBLIC DISCLOSURE timestamp (the event study, rules mode and the
backtester read only these). `hist_events.event_date` = when the thing happened (trade/deal
date, quarter end), stored for reference only; tests/test_deep_disclosure.py fails if research code
reads it. Direction = the hand-set rule's direction, so base rates are measured consistently in
that direction (a negative mean means the market moved the other way).

Sources: live structured rows from Postgres (nightly), and a 5-year NSE backfill of insider trades
and bulk/block deals, a few months per night until complete (resumable via loaded_files). History
materiality uses the point-in-time 20-day traded value only (no market-cap history).
"""

import logging
import time as _time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, cast

import duckdb
from sqlalchemy import Engine, select

from app import taxonomy
from app.collectors.nse import nse_get
from app.config import IST
from app.db.models import (
    BulkBlockDeal,
    BusinessUpdate,
    InsiderTrade,
    Instrument,
    Rating,
    RegulatoryAction,
    ResultsExtract,
    Shareholding,
)
from app.db.session import session_scope
from app.deep import evidence, parsers, rules
from app.history.store import HIST_EVENTS_INSERT
from app.history.universe import fno_on
from app.timeutil import as_ist
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPT_VER = "rules:deep_data_v1"
SET_EVENT_DATE = "UPDATE hist_events SET event_date = ? WHERE event_id = ?"
TABLES = (
    InsiderTrade,
    BulkBlockDeal,
    Shareholding,
    Rating,
    RegulatoryAction,
    ResultsExtract,
    BusinessUpdate,
)


def event_row(
    event_id: str,
    disclosed_at: datetime,
    symbol: str,
    sector: str | None,
    subtype: str,
    source: str,
    title: str,
    url: str | None = None,
    surprise: float | None = None,
) -> tuple[Any, ...]:
    """hist_events row: date/time from the disclosure timestamp, direction from the rule."""
    d = as_ist(disclosed_at).astimezone(IST)
    rule = evidence.rule_value(subtype)
    return (
        event_id,
        d.date(),
        d.time().replace(tzinfo=None),
        symbol,
        sector,
        None,
        subtype,
        "bullish" if rule > 0 else "bearish" if rule < 0 else "neutral",
        source,
        rule,
        surprise,
        title[:300],
        url,
        taxonomy.version(),
        PROMPT_VER,
    )


def write(con: duckdb.DuckDBPyConnection, rows: list[tuple[tuple[Any, ...], date | None]]) -> int:
    if not rows:
        return 0
    con.executemany(HIST_EVENTS_INSERT, [r for r, _ in rows])
    con.executemany(SET_EVENT_DATE, [[ed, r[0]] for r, ed in rows])
    return len(rows)


def _event_date(row: Any) -> date | None:
    for attr in ("event_date", "period_end", "period"):
        v = getattr(row, attr, None)
        if v is not None:
            return v  # type: ignore[no-any-return]
    return None


def sync_structured(
    con: duckdb.DuckDBPyConnection, engine: Engine | None = None, days: int = 3
) -> int:
    """Live material structured rows (Postgres) -> hist_events."""
    since = datetime.now(IST) - timedelta(days=days)
    out: list[tuple[tuple[Any, ...], date | None]] = []
    with session_scope(engine) as s:
        sectors = {i.symbol: i.sector for i in s.scalars(select(Instrument))}
        for model in TABLES:
            cols: Any = model  # every structured table has material / disclosed_at / subtype
            for r in s.scalars(
                select(model).where(cols.material.is_(True), cols.disclosed_at >= since)
            ):
                r = cast(Any, r)
                symbol = getattr(r, "symbol", None)
                subtypes = getattr(r, "subtypes", None) or ([r.subtype] if r.subtype else [])
                if not symbol:
                    continue
                key = getattr(r, "ref", None) or f"{symbol}:{_event_date(r)}"
                for st in subtypes:
                    out.append(
                        (
                            event_row(
                                f"deep:{model.__tablename__}:{key}:{st}",
                                r.disclosed_at,
                                symbol,
                                sectors.get(symbol),
                                st,
                                f"deep:{model.__tablename__}",
                                getattr(r, "title", None) or f"{symbol} {st}",
                                getattr(r, "url", None),
                                getattr(r, "surprise_score", None),
                            ),
                            _event_date(r),
                        )
                    )
    return write(con, out)


# ---- 5-year backfill ------------------------------------------------------------------------
def adv_asof(con: duckdb.DuckDBPyConnection, symbol: str, d: date) -> float | None:
    """Point-in-time 20-session average traded value BEFORE the disclosure date."""
    row = con.execute(
        """SELECT avg(close * volume) FROM (
             SELECT close, volume FROM hist_prices WHERE symbol = ? AND date < ?
             ORDER BY date DESC LIMIT 20)""",
        [symbol, d],
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def history_rows(
    con: duckdb.DuckDBPyConnection,
    insiders: list[parsers.InsiderRow],
    deals: list[parsers.DealRow],
    sectors: dict[str, str],
) -> list[tuple[tuple[Any, ...], date | None]]:
    out: list[tuple[tuple[Any, ...], date | None]] = []
    for r in insiders:
        if r.symbol not in fno_on(con, r.disclosed_at.date()):
            continue
        if not rules.value_materiality(
            "insider", r.value_inr, adv_asof(con, r.symbol, r.disclosed_at.date()), None
        ).material:
            continue
        out.append(
            (
                event_row(
                    f"deep:insider_trades:{r.ref}:{r.subtype}",
                    r.disclosed_at,
                    r.symbol,
                    sectors.get(r.symbol),
                    r.subtype,
                    "deep:insider_trades",
                    f"{r.symbol}: insider {r.side} by {r.counterparty}",
                ),
                r.event_date,
            )
        )
    for d in deals:
        if d.symbol not in fno_on(con, d.event_date):
            continue
        if not rules.value_materiality(
            "deals", d.value_inr, adv_asof(con, d.symbol, d.event_date), None
        ).material:
            continue
        out.append(
            (
                event_row(
                    f"deep:bulk_block_deals:{d.ref}:{d.subtype}",
                    d.disclosed_at,
                    d.symbol,
                    sectors.get(d.symbol),
                    d.subtype,
                    "deep:bulk_block_deals",
                    f"{d.symbol}: {d.kind} deal {d.side} by {d.counterparty}",
                ),
                d.event_date,
            )
        )
    return out


def backfill_structured(
    con: duckdb.DuckDBPyConnection,
    engine: Engine | None = None,
    months_per_run: int = 6,
    fetch: Callable[[str, float], Any] = nse_get,  # injectable for tests
    today: date | None = None,
) -> int:
    """Next `months_per_run` unloaded months (newest first) of the 5-year window."""
    years = float(load_yaml("history.yaml")["years"])
    today = today or datetime.now(IST).date()
    start = today - timedelta(days=int(years * 365))
    done = {
        r[0]
        for r in con.execute(
            "SELECT date FROM loaded_files WHERE kind = 'deep_structured' AND status = 'ok'"
        ).fetchall()
    }
    with session_scope(engine) as s:
        sectors = {i.symbol: i.sector for i in s.scalars(select(Instrument)) if i.sector}
    months: list[date] = []
    m = date(today.year, today.month, 1)
    while m >= date(start.year, start.month, 1):
        if m not in done and m < date(
            today.year, today.month, 1
        ):  # current month: live sync covers it
            months.append(m)
        m = date(m.year - (m.month == 1), (m.month - 2) % 12 + 1, 1)
    cfg = load_yaml("deep_data.yaml")["sources"]
    n = 0
    for m in months[:months_per_run]:
        end = date(m.year + (m.month == 12), m.month % 12 + 1, 1) - timedelta(days=1)
        rng = {"from": m.strftime("%d-%m-%Y"), "to": end.strftime("%d-%m-%Y")}
        try:
            insiders = parsers.parse_insider(fetch(cfg["insider"]["path"].format(**rng), 60.0))
            promoters: dict[str, set[str]] = {}
            for r in insiders:  # promoter names make deal counterparties exact
                if r.counterparty in ("promoter", "promoter_group") and r.person:
                    promoters.setdefault(r.symbol, set()).add(r.person.strip().upper())
            deals = parsers.parse_deals(
                fetch(cfg["bulk_deals"]["path"].format(**rng), 60.0), "bulk", promoters
            )
            deals += parsers.parse_deals(
                fetch(cfg["block_deals"]["path"].format(**rng), 60.0), "block", promoters
            )
        except Exception as exc:  # noqa: BLE001 - retried next night
            log.warning("structured backfill %s failed: %s", m, exc)
            con.execute(
                "INSERT OR REPLACE INTO loaded_files VALUES "
                "('deep_structured', ?, 'error', 0, now())",
                [m],
            )
            continue
        rows = history_rows(con, insiders, deals, sectors)
        n += write(con, rows)
        con.execute(
            "INSERT OR REPLACE INTO loaded_files VALUES ('deep_structured', ?, 'ok', ?, now())",
            [m, len(rows)],
        )
        _time.sleep(1.0)  # polite
    return n
