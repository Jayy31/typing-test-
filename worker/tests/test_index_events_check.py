"""Index event dates checked against NSE Indices press releases (Phase 14 follow-up)."""

from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import IST
from app.db.models import GoliveCheck
from app.golive import index_events as IE
from app.history.store import init_schema
from app.lab import scheduled

NOW = datetime(2026, 10, 20, 22, 55, tzinfo=IST)
YAML = """events:
  - {date: 2024-08-23, symbol: TRENT, index: NIFTY 50, action: inclusion, verified: false}
  - {date: 2024-08-23, symbol: DIVISLAB, index: NIFTY 50, action: exclusion, verified: false}
  - {date: 2025-02-21, symbol: ZOMATO, index: NIFTY 50, action: inclusion, verified: false}
  - {date: 2023-07-07, symbol: LTIM, index: NIFTY 50, action: inclusion, verified: false}
"""
LISTING = """<html><a href="/pr/aug24.pdf">Change in NIFTY 50 constituents (Aug 2024)</a>
<a href="/pr/feb25.html">Change in Index Constituents - Feb 2025</a>
<a href="/about">About us</a></html>"""
AUG = (
    "August 23, 2024 NSE Indices Limited: changes in NIFTY 50. The following companies are "
    "being included: Trent Ltd, Bharat Electronics Ltd. The following companies are being "
    "excluded: Divi's Laboratories Ltd, LTIMindtree Ltd."
)
FEB = (
    "<p>February 24, 2025</p><p>NIFTY 50: the following companies are excluded: Zomato Ltd</p>"
)


def _fetch(pages: dict[str, str]) -> Any:
    def get(url: str, **kw: Any) -> httpx.Response:
        for k, body in pages.items():
            if url.endswith(k):
                return httpx.Response(200, text=body, request=httpx.Request("GET", url))
        return httpx.Response(404, request=httpx.Request("GET", url))

    return get


@pytest.fixture
def con(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> duckdb.DuckDBPyConnection:
    p = tmp_path / "index_events.yaml"
    p.write_text(YAML, encoding="utf-8")
    monkeypatch.setattr(scheduled, "INDEX_EVENTS", p)
    c = duckdb.connect()
    init_schema(c)
    return c


def _loaded(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute("SELECT symbol FROM hist_events WHERE source = 'curated_index'")
    return {r[0] for r in rows.fetchall()}


def test_unverified_entries_are_never_loaded(con: duckdb.DuckDBPyConnection) -> None:
    assert scheduled.load_index_events(con) == 0
    assert _loaded(con) == set()


def test_unreachable_listing_changes_nothing(engine: Engine, con) -> None:
    res = IE.run(engine, NOW, fetch=_fetch({}), con=con)
    assert res["unreachable"] and res["pending"] == 4 and not res["changed"]
    assert _loaded(con) == set()
    with Session(engine) as s:
        assert s.scalars(select(GoliveCheck)).first() is None


def test_verify_mismatch_and_report(engine: Engine, con) -> None:
    pages = {"press-release": LISTING, "aug24.pdf": AUG, "feb25.html": FEB}
    res = IE.run(engine, NOW, fetch=_fetch(pages), con=con)
    got = {e["symbol"]: st for e, st, *_ in res["changed"]}
    assert got == {
        "TRENT": "verified",
        "DIVISLAB": "verified",
        "ZOMATO": "action_mismatch",  # release says excluded
        "LTIM": "not_found",  # 2023 release not on the listing
    }
    assert _loaded(con) == {"TRENT", "DIVISLAB"}  # only verified entries reach Stage 0/1
    with Session(engine) as s:
        row = s.scalars(select(GoliveCheck)).one()
        assert row.kind == "auto" and row.status == "warn"
        assert "ZOMATO" in row.text and "action_mismatch" in row.text
    again = IE.run(engine, NOW, fetch=_fetch(pages), con=con)  # no change -> no new report
    assert again["checked"] == 2 and not again["changed"]


def test_date_mismatch() -> None:
    c = IE.cfg()
    e = {"date": "2024-08-20", "symbol": "TRENT", "index": "NIFTY 50", "action": "inclusion"}
    rel = [IE.Release("u", "t", date(2024, 8, 23), AUG)]
    st, found, _, _ = IE.match(e, rel, IE.names()["TRENT"], c)
    assert (st, found) == ("date_mismatch", date(2024, 8, 23))


def test_listing_and_release_date() -> None:
    links = IE.parse_listing(LISTING, "https://www.niftyindices.com/press-release", ["change in"])
    assert links[0][0] == "https://www.niftyindices.com/pr/aug24.pdf" and len(links) == 2
    assert IE.first_date(AUG) == date(2024, 8, 23)
