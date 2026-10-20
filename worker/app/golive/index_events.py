"""Verify curated index inclusion / exclusion dates against NSE Indices press releases.

`data/index_events.yaml` was filled from general knowledge, so no entry is used by the Strategy
Lab until a press release confirms it. Nightly (while any entry is unverified): read the press
release listing, open the matching releases (PDF or HTML), and for each entry look for a release
that names the index and the company with the right action. Results go to the history store
(`index_event_checks`); `app/lab/scheduled.load_index_events` loads only verified entries.
Mismatches (other date, other action, not found) are reported on the Go-live page and Telegram.
An unreachable listing changes nothing: entries stay unverified and excluded.
"""

import csv
import io
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import Engine

from app.collectors import nse_http
from app.config import IST
from app.db.models import GoliveCheck
from app.db.session import session_scope
from app.golive.checks import Check
from app.lab.scheduled import DATE_PATTERNS, _to_date, event_key, index_events
from app.notify.outbox import enqueue_alert
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
SYMBOLS = Path(__file__).resolve().parents[3] / "data" / "symbol_master.csv"
LINK = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
TAG = re.compile(r"<[^>]+>")
INCL = re.compile(r"\binclu(?:de|ded|sion|sions|ding)\b", re.I)
EXCL = re.compile(r"\bexclu(?:de|ded|sion|sions|ding)\b", re.I)


def cfg() -> dict[str, Any]:
    return dict(load_yaml("golive.yaml")["index_events"])


@dataclass
class Release:
    url: str
    title: str
    day: date | None
    text: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9& ]", " ", s.lower())).strip()


def names() -> dict[str, list[str]]:
    """Symbol -> names to look for (symbol, company name without 'Ltd', aliases)."""
    out: dict[str, list[str]] = {}
    for r in csv.DictReader(SYMBOLS.read_text(encoding="utf-8").splitlines()):
        base = re.sub(r"\b(limited|ltd)\b\.?", "", r["name"], flags=re.I)
        alias = [a for a in (r.get("aliases") or "").split("|") if len(a) > 3]
        out[r["symbol"]] = [_norm(x) for x in [r["symbol"], base, *alias] if _norm(x)]
    return out


def first_date(text: str) -> date | None:
    """Release date = the first full date in the opening part of the release."""
    head = text[:600]
    found = []
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(head):
            d = _to_date(m.groups(), kind)
            if d:
                found.append((m.start(), d))
    return min(found)[1] if found else None


def parse_listing(html: str, base: str, patterns: list[str]) -> list[tuple[str, str]]:
    out, seen = [], set()
    for href, label in LINK.findall(html):
        title = re.sub(r"\s+", " ", TAG.sub(" ", label)).strip()
        if not any(p in title.lower() for p in patterns):
            continue
        url = str(httpx.URL(base).join(href))
        if url not in seen:
            seen.add(url)
            out.append((url, title))
    return out


def release_text(body: bytes, content_type: str) -> str:
    if "pdf" in content_type or body[:4] == b"%PDF":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(body))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:10])
    return re.sub(r"\s+", " ", TAG.sub(" ", body.decode("utf-8", "replace")))


def _action_near(text: str, pos: int) -> str | None:
    """The inclusion / exclusion keyword closest BEFORE the company name (section heading)."""
    before = text[:pos]
    i = max((m.start() for m in INCL.finditer(before)), default=-1)
    e = max((m.start() for m in EXCL.finditer(before)), default=-1)
    if i < 0 and e < 0:
        return None
    return "inclusion" if i > e else "exclusion"


def match(
    e: dict[str, Any], releases: list[Release], aliases: list[str], c: dict[str, Any]
) -> tuple[str, date | None, str | None, str]:
    """-> (status, found_date, url, detail). Status: verified / date_mismatch / action_mismatch
    / not_found."""
    want = date.fromisoformat(str(e["date"]))
    index = _norm(str(e["index"]))
    best: tuple[int, Release, str | None] | None = None
    for r in releases:
        if r.day is None or abs((r.day - want).days) > int(c["search_window_days"]):
            continue
        t = _norm(r.text)
        if index not in t:
            continue
        hits = [t.find(a) for a in aliases if a and t.find(a) >= 0]
        if not hits:
            continue
        gap = abs((r.day - want).days)
        if best is None or gap < best[0]:
            best = (gap, r, _action_near(t, min(hits)))
    if best is None:
        return "not_found", None, None, f"no release naming {e['symbol']} and {e['index']}"
    gap, r, action = best
    if action is not None and action != e["action"]:
        return "action_mismatch", r.day, r.url, f"release says {action}, YAML says {e['action']}"
    if gap > int(c["date_tolerance_days"]):
        return "date_mismatch", r.day, r.url, f"release dated {r.day}, YAML says {want}"
    return "verified", r.day, r.url, r.title[:120]


Fetch = Callable[..., httpx.Response]


def collect(fetch: Fetch, c: dict[str, Any]) -> tuple[list[Release], str | None]:
    """All candidate releases, or (empty, reason) when the listing cannot be read."""
    links: list[tuple[str, str]] = []
    errors = []
    for url in c["listing_urls"]:
        try:
            r = fetch(url, timeout=float(c["timeout_s"]))
            r.raise_for_status()
            links += parse_listing(r.text, url, list(c["title_patterns"]))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {type(exc).__name__}")
    if not links:
        return [], "; ".join(errors) or "no press-release links found on the listing pages"
    out = []
    for url, title in links[: int(c["max_releases"])]:
        try:
            r = fetch(url, timeout=float(c["timeout_s"]))
            r.raise_for_status()
            text = release_text(r.content, r.headers.get("content-type", ""))
        except Exception as exc:  # noqa: BLE001
            log.info("index release %s: %s", url, type(exc).__name__)
            continue
        out.append(Release(url, title, first_date(text) or first_date(title), text))
    return out, None


def run(
    engine: Engine | None = None,
    now: datetime | None = None,
    fetch: Fetch = nse_http.get,
    con: Any = None,
) -> dict[str, Any]:
    """Check every entry not yet verified; store results; report changes."""
    from app.history.store import connect

    now = now or datetime.now(IST)
    c = cfg()
    events = index_events()

    def go(con: Any) -> dict[str, Any]:
        prev = {
            r[0]: r[1]
            for r in con.execute("SELECT event_key, status FROM index_event_checks").fetchall()
        }
        todo = [
            e
            for e in events
            if e.get("verified") is not True and prev.get(event_key(e)) != "verified"
        ]
        if not todo:
            return {"checked": 0, "changed": [], "unreachable": None}
        releases, why = collect(fetch, c)
        if why:
            return {"checked": 0, "changed": [], "unreachable": why, "pending": len(todo)}
        al = names()
        changed = []
        for e in todo:
            st, found, url, detail = match(
                e, releases, al.get(e["symbol"], [e["symbol"].lower()]), c
            )
            con.execute(
                "INSERT OR REPLACE INTO index_event_checks VALUES (?, ?, ?, ?, ?, ?)",
                [event_key(e), st, found, url, detail, now.replace(tzinfo=None)],
            )
            if prev.get(event_key(e)) != st:
                changed.append((e, st, found, url, detail))
        if changed:  # reload the verified set into hist_events right away
            from app.lab.scheduled import load_index_events

            load_index_events(con)
        return {"checked": len(todo), "changed": changed, "unreachable": None}

    if con is not None:
        res = go(con)
    else:
        with connect(write=True) as w:
            res = go(w)
    if res["changed"]:
        report(engine, res["changed"], now)
    return res


def report(engine: Engine | None, changed: list[Any], now: datetime) -> None:
    bad = [x for x in changed if x[1] != "verified"]
    lines = [
        f"{e['symbol']} {e['action']} {e['index']} {e['date']}: {st}"
        + (f" ({detail})" if st != "verified" else "")
        + (f" {url}" if url else "")
        for e, st, _f, url, detail in changed
    ]
    status = "warn" if bad else "ok"
    text = (
        "INDEX EVENT DATES vs NSE INDICES PRESS RELEASES\n"
        f"{len(changed) - len(bad)} verified, {len(bad)} mismatched or not found "
        "(excluded from Stage 0/1 until data/index_events.yaml matches)\n" + "\n".join(lines[:20])
    )
    with session_scope(engine) as s:
        s.add(
            GoliveCheck(
                kind="auto",
                created_at=now,
                status=status,
                text=text,
                results_json={
                    "title": "Index event dates (automatic)",
                    "checks": [
                        {
                            "name": "index_events",
                            "status": status,
                            "summary": f"{len(changed) - len(bad)} verified, {len(bad)} mismatched",
                            "details": lines,
                        }
                    ],
                },
            )
        )
        if bad:
            enqueue_alert(s, "health", text, f"index_events:{now:%Y-%m-%d}")


def check() -> Check:
    """For the 24-hour go-live report (UNVERIFIED item 50)."""
    try:
        from app.history.store import connect

        with connect() as con:
            rows = dict(
                (r[0], r[1:])
                for r in con.execute(
                    "SELECT event_key, status, found_date, detail FROM index_event_checks"
                ).fetchall()
            )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "index_events", "warn", f"history store not readable yet: {type(exc).__name__}"
        )
    lines, counts = [], {"verified": 0, "pending": 0, "mismatch": 0}
    for e in index_events():
        k = event_key(e)
        if e.get("verified") is True:
            st = "verified"
        else:
            st = rows.get(k, ("pending",))[0]
        bucket = st if st in ("verified", "pending") else "mismatch"
        counts[bucket] += 1
        if st != "verified":
            lines.append(f"{k}: {st}" + (f" ({rows[k][2]})" if k in rows and rows[k][2] else ""))
    status = "ok" if counts["pending"] == counts["mismatch"] == 0 else "warn"
    return Check(
        "index_events",
        status,
        f"index event dates: {counts['verified']} verified, {counts['pending']} pending, "
        f"{counts['mismatch']} mismatched (only verified ones are used by the lab)",
        lines,
    )
