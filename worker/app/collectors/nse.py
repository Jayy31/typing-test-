"""NSE public endpoints: corporate announcements, FII/DII flows, event calendar.

NSE blocks plain scripted requests: we warm up a session on the homepage (cookies) and
call the JSON API politely. Parsers are pure functions so they can be tested offline.
These endpoints are unofficial and change without notice; failures are logged, not fatal.
"""

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, select

from app.collectors import nse_http
from app.collectors.base import Collector
from app.collectors.news_rss import RawNews, store_items
from app.config import IST
from app.db.config_store import set_config
from app.db.models import EventCalendar, Instrument, MacroSnapshot
from app.db.session import session_scope

log = logging.getLogger(__name__)
BASE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE,
}


def nse_get(path: str, timeout: float = 20.0) -> Any:
    with nse_http.client(headers=HEADERS, timeout=timeout, follow_redirects=True) as c:
        c.get(BASE)  # cookie warm-up
        resp = c.get(f"{BASE}{path}")
        resp.raise_for_status()
        return resp.json()


def _parse_dt(value: str, fmts: tuple[str, ...]) -> datetime | None:
    for fmt in fmts:
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def parse_announcements(rows: list[dict[str, Any]]) -> list[RawNews]:
    """NSE corporate-announcements JSON -> RawNews (official source, highest weight)."""
    out: list[RawNews] = []
    for r in rows:
        symbol = str(r.get("symbol", "")).strip()
        desc = str(r.get("desc", "")).strip()
        text = str(r.get("attchmntText", "") or "").strip()
        url = str(r.get("attchmntFile", "") or "").strip()
        if not symbol or not url or not (desc or text):
            continue
        published = _parse_dt(str(r.get("an_dt", "")), ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"))
        out.append(
            RawNews(
                source="nse_announcements",
                kind="official",
                title=f"{symbol}: {desc}"[:300],
                body=text or None,
                url=url,
                published_at=published,
            )
        )
    return out


def parse_fii_dii(rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    """NSE fiidiiTradeReact JSON -> (fii_net, dii_net) in Rs crore."""
    fii = dii = None
    for r in rows:
        cat = str(r.get("category", "")).upper()
        try:
            net = float(str(r.get("netValue", "")).replace(",", ""))
        except ValueError:
            continue
        if cat.startswith("FII") or cat.startswith("FPI"):
            fii = net
        elif cat.startswith("DII"):
            dii = net
    return (fii, dii) if fii is not None and dii is not None else None


def parse_event_calendar(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """NSE event-calendar JSON -> event dicts (results, board meetings, dividends...)."""
    events: list[dict[str, Any]] = []
    for r in rows:
        d = _parse_dt(str(r.get("date", "")), ("%d-%b-%Y", "%Y-%m-%d"))
        symbol = str(r.get("symbol", "")).strip()
        if not d or not symbol:
            continue
        purpose = str(r.get("purpose", "")).lower()
        etype = "results" if "result" in purpose else "board_meeting"
        if "dividend" in purpose:
            etype = "dividend"
        events.append(
            {
                "symbol": symbol,
                "event_type": etype,
                "event_date": d.date(),
                "details": {"purpose": r.get("purpose"), "desc": r.get("bm_desc")},
            }
        )
    return events


class AnnouncementsCollector(Collector[RawNews]):
    name = "nse_announcements"

    def fetch(self) -> Any:
        return nse_get("/api/corporate-announcements?index=equities", self.timeout_s)

    def parse(self, raw: Any) -> list[RawNews]:
        return parse_announcements(raw)

    def store(self, items: list[RawNews], engine: Engine | None) -> int:
        return store_items(items, engine)


class FiiDiiCollector(Collector[tuple[float, float]]):
    name = "nse_fii_dii"

    def fetch(self) -> Any:
        return nse_get("/api/fiidiiTradeReact", self.timeout_s)

    def parse(self, raw: Any) -> list[tuple[float, float]]:
        flows = parse_fii_dii(raw)
        if flows is None:
            raise RuntimeError("FII/DII data not found in NSE response")
        return [flows]

    def store(self, items: list[tuple[float, float]], engine: Engine | None) -> int:
        ts = datetime.now(IST).replace(hour=18, minute=0, second=0, microsecond=0)
        with session_scope(engine) as s:
            snap = s.get(MacroSnapshot, ts) or MacroSnapshot(ts=ts)
            snap.fii_net, snap.dii_net = items[0]
            s.add(snap)
        return 1


class EventCalendarCollector(Collector[dict[str, Any]]):
    """Results/board-meeting dates for F&O stocks. Idempotent on (symbol, type, date)."""

    name = "nse_event_calendar"

    def fetch(self) -> Any:
        return nse_get("/api/event-calendar", self.timeout_s)

    def parse(self, raw: Any) -> list[dict[str, Any]]:
        return parse_event_calendar(raw)

    def store(self, items: list[dict[str, Any]], engine: Engine | None) -> int:
        return store_events(items, engine)


def store_events(events: list[dict[str, Any]], engine: Engine | None = None) -> int:
    added = 0
    with session_scope(engine) as s:
        fno = {i.symbol for i in s.scalars(select(Instrument)) if i.is_fno}
        for e in events:
            if e.get("symbol") and e["symbol"] not in fno:
                continue
            exists = s.scalar(
                select(EventCalendar.id).where(
                    EventCalendar.symbol == e.get("symbol"),
                    EventCalendar.event_type == e["event_type"],
                    EventCalendar.event_date == e["event_date"],
                )
            )
            if exists:
                continue
            s.add(
                EventCalendar(
                    symbol=e.get("symbol"),
                    sector=e.get("sector"),
                    event_type=e["event_type"],
                    event_date=e["event_date"],
                    source=e.get("source", "nse"),
                    details=e.get("details"),
                )
            )
            added += 1
    return added


def today() -> date:
    return datetime.now(IST).date()


BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"


def parse_ban_list(text: str) -> list[str]:
    """NSE fo_secban.csv: a title line, then rows like '1,SYMBOL'. Empty list is valid."""
    symbols: list[str] = []
    for line in text.splitlines()[1:]:
        cols = [c.strip() for c in line.split(",")]
        if len(cols) >= 2 and cols[0].isdigit() and cols[1]:
            symbols.append(cols[1].upper())
    return symbols


class FnoBanCollector(Collector[str]):
    name = "nse_fno_ban"

    def fetch(self) -> Any:
        resp = nse_http.get(BAN_URL, headers=HEADERS, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.text

    def parse(self, raw: Any) -> list[str]:
        return parse_ban_list(str(raw))

    def store(self, items: list[str], engine: Engine | None) -> int:
        with session_scope(engine) as s:
            set_config(s, "fno_ban", {"date": today().isoformat(), "symbols": items})
        return len(items)


def classify_action(subject: str) -> str | None:
    low = subject.lower()
    if "split" in low or "sub-division" in low or "sub division" in low:
        return "split"
    if "bonus" in low:
        return "bonus"
    if "demerger" in low or "scheme of arrangement" in low or "spin" in low:
        return "demerger"
    if "special dividend" in low:
        return "special_dividend"
    if "dividend" in low:
        return "dividend"
    return None


def parse_corporate_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """NSE corporate-actions JSON -> events keyed by ex-date (split/bonus/demerger/dividend)."""
    events: list[dict[str, Any]] = []
    for r in rows:
        etype = classify_action(str(r.get("subject", "")))
        d = _parse_dt(str(r.get("exDate", "")), ("%d-%b-%Y", "%Y-%m-%d"))
        symbol = str(r.get("symbol", "")).strip()
        if etype and d and symbol:
            events.append(
                {
                    "symbol": symbol,
                    "event_type": etype,
                    "event_date": d.date(),
                    "details": {"subject": r.get("subject")},
                }
            )
    return events


class CorporateActionsCollector(Collector[dict[str, Any]]):
    name = "nse_corporate_actions"

    def fetch(self) -> Any:
        return nse_get("/api/corporates-corporateActions?index=equities", self.timeout_s)

    def parse(self, raw: Any) -> list[dict[str, Any]]:
        return parse_corporate_actions(raw)

    def store(self, items: list[dict[str, Any]], engine: Engine | None) -> int:
        return store_events(items, engine)


collect_announcements = AnnouncementsCollector().run
collect_fii_dii = FiiDiiCollector().run
collect_event_calendar = EventCalendarCollector().run
collect_fno_ban = FnoBanCollector().run
collect_corporate_actions = CorporateActionsCollector().run
