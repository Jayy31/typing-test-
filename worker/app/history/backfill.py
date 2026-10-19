"""5-year backfill into the history store (run on the VM; see docs/setup/phase-4.md).

    python -m app.history.backfill prices  --from 2021-10-01 --to 2026-10-16
    python -m app.history.backfill options --from ... --to ...
    python -m app.history.backfill index   --from ... --to ...
    python -m app.history.backfill all     [--years 5]
    python -m app.history.backfill daily   # today's files (scheduled after the close)

Idempotent and resumable: each (kind, date) is recorded in loaded_files; raw files are cached.
Weekends are skipped; days with no NSE file are recorded as 'no_file' (holidays).
"""

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta

import duckdb

from app.config import IST
from app.history.nse_files import (
    Downloader,
    FormatError,
    fo_kind,
    parse_equity,
    parse_fo,
    parse_index,
)
from app.history.store import connect
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
KINDS = ("prices", "options", "futures", "index")


def weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _done(con: duckdb.DuckDBPyConnection, kind: str) -> set[date]:
    rows = con.execute(
        "SELECT date FROM loaded_files WHERE kind = ? AND status IN ('ok', 'no_file')", [kind]
    ).fetchall()
    return {r[0] for r in rows}


def _mark(con: duckdb.DuckDBPyConnection, kind: str, d: date, status: str, rows: int) -> None:
    con.execute(
        "INSERT OR REPLACE INTO loaded_files VALUES (?, ?, ?, ?, ?)",
        [kind, d, status, rows, datetime.now(IST).replace(tzinfo=None)],
    )


def load_prices(con: duckdb.DuckDBPyConnection, dl: Downloader, d: date) -> int:
    raw = dl.get("equity", d)
    if raw is None:
        return -1
    rows = parse_equity(raw, d)
    con.executemany("INSERT OR REPLACE INTO hist_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def load_options(con: duckdb.DuckDBPyConnection, dl: Downloader, d: date) -> int:
    raw = dl.get(fo_kind(d), d)
    if raw is None:
        return -1
    parsed = parse_fo(raw, d)
    con.executemany(
        "INSERT OR REPLACE INTO hist_options VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        parsed.options,
    )
    # F&O universe as of that day (no survivorship bias): every stock with options that day
    lots = parsed.lots
    universe = sorted({r[1] for r in parsed.options})
    con.executemany(
        "INSERT OR REPLACE INTO hist_fno_universe VALUES (?, ?, ?)",
        [(d, s, lots.get(s)) for s in universe],
    )
    return len(parsed.options)


def load_futures(con: duckdb.DuckDBPyConnection, dl: Downloader, d: date) -> int:
    """Stock futures from the same F&O file (served from the download cache when already fetched)."""
    raw = dl.get(fo_kind(d), d)
    if raw is None:
        return -1
    rows = parse_fo(raw, d).futures
    con.executemany("INSERT OR REPLACE INTO hist_futures VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def load_index(con: duckdb.DuckDBPyConnection, dl: Downloader, d: date) -> int:
    raw = dl.get("index", d)
    if raw is None:
        return -1
    rows = parse_index(raw, d)
    con.executemany("INSERT OR REPLACE INTO hist_index VALUES (?, ?, ?)", rows)
    return len(rows)


LOADERS: dict[str, Callable[[duckdb.DuckDBPyConnection, Downloader, date], int]] = {
    "prices": load_prices,
    "options": load_options,
    "futures": load_futures,
    "index": load_index,
}


def run(
    kind: str,
    start: date,
    end: date,
    dl: Downloader | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
    progress: bool = True,
) -> dict[str, int]:
    """Load every missing weekday in [start, end]. Returns counts by status."""
    dl = dl or Downloader()
    stats = {"ok": 0, "no_file": 0, "error": 0, "skipped": 0, "rows": 0}

    def body(c: duckdb.DuckDBPyConnection) -> None:
        done = _done(c, kind)
        days = [d for d in weekdays(start, end) if d not in done]
        stats["skipped"] = len(weekdays(start, end)) - len(days)
        for i, d in enumerate(days, 1):
            try:
                n = LOADERS[kind](c, dl, d)
            except FormatError as exc:
                _mark(c, kind, d, "format_error", 0)
                stats["error"] += 1
                log.error("%s %s: %s", kind, d, exc)
                raise  # a format problem affects every day: stop and fix config/history.yaml
            except Exception as exc:  # noqa: BLE001 - network: record and continue
                _mark(c, kind, d, "error", 0)
                stats["error"] += 1
                log.warning("%s %s failed: %s", kind, d, exc)
                continue
            if n < 0:
                _mark(c, kind, d, "no_file", 0)
                stats["no_file"] += 1
            else:
                _mark(c, kind, d, "ok", n)
                stats["ok"] += 1
                stats["rows"] += n
            if progress and i % 25 == 0:
                print(f"{kind}: {i}/{len(days)} days ({d})", flush=True)

    if con is not None:
        body(con)
    else:
        with connect(write=True) as c:
            body(c)
    return stats


def daily_job() -> int:
    """After the close: today's (and any missed recent) files for all kinds."""
    end = datetime.now(IST).date()
    start = end - timedelta(days=7)
    total = 0
    for kind in KINDS:
        total += run(kind, start, end, progress=False)["rows"]
    return total


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("kind", choices=[*KINDS, "all", "daily"])
    p.add_argument("--from", dest="start", type=date.fromisoformat)
    p.add_argument("--to", dest="end", type=date.fromisoformat)
    p.add_argument("--years", type=int, default=int(load_yaml("history.yaml")["years"]))
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if a.kind == "daily":
        print(daily_job())
        return 0
    end = a.end or datetime.now(IST).date()
    start = a.start or end.replace(year=end.year - a.years)
    for kind in KINDS if a.kind == "all" else (a.kind,):
        print(kind, run(kind, start, end), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
