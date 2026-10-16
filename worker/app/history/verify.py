"""Verification report for the history store (run on the VM after a backfill):

    python -m app.history.verify

Prints row counts, date ranges, missing weekdays per kind (excluding days NSE had no file),
load errors, symbols with gaps, and option/IV coverage. Exit code 1 if anything needs attention.
"""

import sys
from datetime import date

import duckdb

from app.history.backfill import KINDS, weekdays
from app.history.store import connect

TABLES = [
    "hist_prices",
    "hist_options",
    "hist_index",
    "hist_fno_universe",
    "hist_raw_news",
    "hist_events",
    "event_reactions",
    "analogue_stats",
    "hist_iv",
    "hist_atm_iv",
]


def report(con: duckdb.DuckDBPyConnection) -> tuple[list[str], bool]:
    lines: list[str] = []
    problems = False
    lines.append("== Row counts and date ranges ==")
    for t in TABLES:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # type: ignore[index]
        rng = ""
        cols = {r[0] for r in con.execute(f"DESCRIBE {t}").fetchall()}
        if "date" in cols and n:
            lo, hi = con.execute(f"SELECT min(date), max(date) FROM {t}").fetchone()  # type: ignore[misc]
            rng = f"  {lo} .. {hi}"
        lines.append(f"{t:20s} {n:>12,}{rng}")

    lines.append("\n== Load status per kind ==")
    for kind in KINDS:
        rows = con.execute(
            "SELECT status, count(*), min(date), max(date) FROM loaded_files WHERE kind = ? GROUP BY 1 ORDER BY 1",
            [kind],
        ).fetchall()
        if not rows:
            lines.append(f"{kind}: nothing loaded")
            problems = True
            continue
        for status, n, lo, hi in rows:
            lines.append(f"{kind:8s} {status:12s} {n:>6} days  {lo} .. {hi}")
            if status in ("error", "format_error"):
                problems = True
        lo = min(r[2] for r in rows)
        hi = max(r[3] for r in rows)
        seen = {
            r[0]
            for r in con.execute("SELECT date FROM loaded_files WHERE kind = ?", [kind]).fetchall()
        }
        missing = [d for d in weekdays(lo, hi) if d not in seen]
        if missing:
            problems = True
            lines.append(f"  {kind}: {len(missing)} weekday(s) never attempted, e.g. {missing[:5]}")
        errs = con.execute(
            "SELECT date FROM loaded_files WHERE kind = ? AND status = 'error' ORDER BY date LIMIT 5",
            [kind],
        ).fetchall()
        if errs:
            lines.append(f"  {kind}: re-run to retry error days, e.g. {[e[0] for e in errs]}")

    lines.append("\n== Price gaps (F&O stocks missing on days the market traded) ==")
    gaps = con.execute("""
        WITH days AS (SELECT DISTINCT date FROM hist_prices),
             fno AS (SELECT DISTINCT symbol FROM hist_fno_universe),
             have AS (SELECT symbol, count(*) n FROM hist_prices WHERE symbol IN (SELECT symbol FROM fno) GROUP BY 1)
        SELECT symbol, (SELECT count(*) FROM days) - n AS missing FROM have
        WHERE (SELECT count(*) FROM days) - n > 20 ORDER BY missing DESC LIMIT 15""").fetchall()
    lines += [
        f"  {s}: {m} days missing (listing/delisting/ban periods are normal)" for s, m in gaps
    ] or ["  none above 20 days"]

    lines.append("\n== Options coverage ==")
    for d_lo, d_hi, n_sym, n_rows in con.execute("""
        SELECT min(date), max(date), count(DISTINCT symbol), count(*) FROM hist_options""").fetchall():
        lines.append(f"  {n_sym} stocks, {n_rows:,} option rows, {d_lo} .. {d_hi}")
    iv_days = con.execute("SELECT count(DISTINCT date) FROM hist_atm_iv").fetchone()[0]  # type: ignore[index]
    lines.append(f"  ATM IV history: {iv_days} days")
    return lines, problems


def main() -> int:
    with connect() as con:
        lines, problems = report(con)
    print("\n".join(lines))
    print("\nRESULT:", "CHECK THE ITEMS ABOVE" if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["date"]
