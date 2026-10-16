"""Historical news for the event study: NSE corporate announcements (5 years, monthly chunks)
and GDELT (DOC API, roughly the last 3 months only; going forward it is collected daily).

    python -m app.history.news_history announcements --from 2021-10-01 --to 2026-10-16
    python -m app.history.news_history gdelt --days 7
"""

import argparse
import hashlib
import logging
import sys
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import httpx

from app.collectors.nse import nse_get, parse_announcements
from app.config import IST
from app.history.store import connect
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


def news_id(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}|{title}".encode()).hexdigest()[:32]


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    out, d = [], start.replace(day=1)
    while d <= end:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        out.append((max(d, start), min(nxt - timedelta(days=1), end)))
        d = nxt
    return out


def store_news(con: duckdb.DuckDBPyConnection, rows: list[tuple[Any, ...]]) -> int:
    """rows: (news_id, ts, symbol, title, body, url, source). Existing ids are kept (idempotent)."""
    before = con.execute("SELECT count(*) FROM hist_raw_news").fetchone()[0]  # type: ignore[index]
    con.executemany(
        "INSERT OR IGNORE INTO hist_raw_news (news_id, ts, symbol, title, body, url, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return int(con.execute("SELECT count(*) FROM hist_raw_news").fetchone()[0]) - int(before)  # type: ignore[index]


def announcements(start: date, end: date, con: duckdb.DuckDBPyConnection) -> int:
    path = load_yaml("history.yaml")["sources"]["announcements"]["path"]
    added = 0
    for lo, hi in month_chunks(start, end):
        key = f"announcements:{lo.isoformat()}"
        if con.execute("SELECT 1 FROM store_meta WHERE key = ?", [key]).fetchone():
            continue
        try:
            raw = nse_get(
                path.format(
                    from_ddmmyyyy=lo.strftime("%d-%m-%Y"), to_ddmmyyyy=hi.strftime("%d-%m-%Y")
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("announcements %s..%s failed: %s", lo, hi, exc)
            continue
        items = parse_announcements(raw if isinstance(raw, list) else raw.get("data", []))
        rows = [
            (
                news_id(i.url, i.title),
                i.published_at.astimezone(IST).replace(tzinfo=None),  # IST, naive
                i.title.split(":")[0],
                i.title,
                i.body,
                i.url,
                "nse_announcements",
            )
            for i in items
            if i.published_at is not None  # no timestamp -> no reliable reaction day
        ]
        added += store_news(con, rows)
        if hi < datetime.now(IST).date() - timedelta(days=3):  # only freeze complete past months
            con.execute("INSERT OR REPLACE INTO store_meta VALUES (?, ?)", [key, str(len(rows))])
        time.sleep(float(load_yaml("history.yaml")["http"]["min_interval_s"]))
    return added


def parse_gdelt(payload: dict[str, Any]) -> list[tuple[Any, ...]]:
    rows = []
    for a in payload.get("articles", []) or []:
        url, title = a.get("url"), a.get("title")
        seen = a.get("seendate")  # e.g. 20261015T093000Z
        if not (url and title and seen):
            continue
        # GDELT times are UTC; the history stores IST (reaction-day rules use IST)
        utc = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        ts = utc.astimezone(IST).replace(tzinfo=None)
        rows.append(
            (news_id(url, title), ts, None, title, None, url, f"gdelt:{a.get('domain', '')}")
        )
    return rows


def gdelt(days: int, con: duckdb.DuckDBPyConnection, http: httpx.Client | None = None) -> int:
    cfg = load_yaml("history.yaml")["sources"]["gdelt"]
    http = http or httpx.Client(timeout=30)
    end = datetime.now(IST).replace(tzinfo=None)
    params = {
        "query": cfg["query"],
        "mode": "artlist",
        "format": "json",
        "maxrecords": cfg["max_records"],
        "startdatetime": (end - timedelta(days=days)).strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    resp = http.get(cfg["url"], params=params)
    resp.raise_for_status()
    return store_news(con, parse_gdelt(resp.json()))


def gdelt_daily_job() -> int:
    from app.config import get_settings

    if not get_settings().gdelt_enabled:
        return 0
    with connect(write=True) as con:
        return gdelt(2, con)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["announcements", "gdelt"])
    p.add_argument("--from", dest="start", type=date.fromisoformat)
    p.add_argument("--to", dest="end", type=date.fromisoformat)
    p.add_argument("--days", type=int, default=7)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    with connect(write=True) as con:
        if a.what == "gdelt":
            print("added", gdelt(a.days, con))
        else:
            end = a.end or datetime.now(IST).date()
            start = a.start or end.replace(year=end.year - int(load_yaml("history.yaml")["years"]))
            print("added", announcements(start, end, con))
    return 0


if __name__ == "__main__":
    sys.exit(main())
