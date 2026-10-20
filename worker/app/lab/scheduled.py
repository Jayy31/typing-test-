"""Scheduled events with their intimation date (`hist_scheduled`, Phase 14).

From NSE board-meeting intimations in the announcement history (`hist_raw_news`): the
announcement time is when the date became public, the meeting date is parsed from the text.
Only meetings that consider financial results are kept (event type `results`). A pre-event
entry may use a row only if `intimated_on` is early enough (app/lab/backtest.py).
"""

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

BOARD = re.compile(r"board\s+meeting", re.I)
RESULTS = re.compile(r"(financial\s+results|quarterly\s+results|audited|unaudited)", re.I)
DATE_PATTERNS = [
    (re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})"), "dmy"),
    (re.compile(r"(\d{1,2})[-\s]([A-Za-z]{3,9})[-,\s]+(\d{4})"), "dMy"),
    (re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})"), "Mdy"),
]
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


def _to_date(groups: tuple[str, ...], kind: str) -> date | None:
    try:
        if kind == "dmy":
            return date(int(groups[2]), int(groups[1]), int(groups[0]))
        if kind == "dMy":
            m = MONTHS.get(groups[1][:3].lower())
            return date(int(groups[2]), m, int(groups[0])) if m else None
        m = MONTHS.get(groups[0][:3].lower())
        return date(int(groups[2]), m, int(groups[1])) if m else None
    except ValueError:
        return None


def meeting_date(text: str, announced: date) -> date | None:
    """The first date in the text on or after the announcement day (within 60 days)."""
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(text):
            d = _to_date(m.groups(), kind)
            if d and announced <= d and (d - announced).days <= 60:
                return d
    return None


def parse(rows: list[tuple[Any, ...]]) -> list[tuple[str, str, date, date, str, str]]:
    out: dict[tuple[str, str, date], tuple[str, str, date, date, str, str]] = {}
    for news_id, ts, sym, title, body in rows:
        text = f"{title or ''} {body or ''}"
        if not sym or not BOARD.search(text) or not RESULTS.search(text):
            continue
        announced = ts.date() if isinstance(ts, datetime) else ts
        d = meeting_date(text, announced)
        if d is None:
            continue
        key = (sym, "results", d)
        prev = out.get(key)
        if prev is None or announced < prev[3]:  # earliest intimation wins
            out[key] = (sym, "results", d, announced, "nse_board_meeting", str(news_id))
    return list(out.values())


def build(con: duckdb.DuckDBPyConnection) -> int:
    rows = con.execute(
        "SELECT news_id, ts, symbol, title, body FROM hist_raw_news WHERE symbol IS NOT NULL "
        "AND (lower(title) LIKE '%board meeting%' OR lower(body) LIKE '%board meeting%')"
    ).fetchall()
    found = parse(rows)
    if not found:
        return 0
    con.executemany("INSERT OR REPLACE INTO hist_scheduled VALUES (?, ?, ?, ?, ?, ?)", found)
    return len(found)


INDEX_EVENTS = Path(__file__).resolve().parents[3] / "data" / "index_events.yaml"


def event_key(e: dict[str, Any]) -> str:
    return f"{e['symbol']}|{e['action']}|{e['index']}|{e['date']}"


def index_events(path: Any = None) -> list[dict[str, Any]]:
    import yaml

    p = path or INDEX_EVENTS
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("events") or []


def verified_keys(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute("SELECT event_key FROM index_event_checks WHERE status = 'verified'")
    return {r[0] for r in rows.fetchall()}


def load_index_events(con: duckdb.DuckDBPyConnection, path: Any = None) -> int:
    """Curated index inclusion / exclusion announcements -> hist_events (structured events).

    Only VERIFIED entries are loaded: `verified: true` in the YAML, or confirmed against an NSE
    Indices press release by app/golive/index_events.py. Unverified or mismatched entries are
    removed, so Stage 0 / Stage 1 never test a date from general knowledge."""
    from app.history.store import HIST_EVENTS_INSERT

    ok = verified_keys(con)
    out = []
    for e in index_events(path):
        if not (e.get("verified") is True or event_key(e) in ok):
            continue
        d = date.fromisoformat(str(e["date"]))
        incl = e["action"] == "inclusion"
        out.append(
            (
                f"index|{e['symbol']}|{e['action']}|{d}",
                d,
                None,
                e["symbol"],
                None,
                None,
                "index_inclusion" if incl else "index_exclusion",
                "bullish" if incl else "bearish",
                "curated_index",
                0.5 if incl else -0.5,
                None,
                f"{e['symbol']} {e['action']} in {e['index']}",
                None,
                "tx2",
                "curated",
            )
        )
    con.execute("DELETE FROM hist_events WHERE source = 'curated_index'")
    if out:
        con.executemany(HIST_EVENTS_INSERT, out)
    return len(out)
