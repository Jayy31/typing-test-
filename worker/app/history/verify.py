"""Verification report for the history store (run on the VM after a backfill):

    python -m app.history.verify

Prints row counts, date ranges, missing weekdays per kind (excluding days NSE had no file),
load errors, symbols with gaps, and option/IV coverage. Exit code 1 if anything needs attention.
"""

import sys
from datetime import date
from typing import Any

import duckdb

from app.history.backfill import KINDS, weekdays
from app.history.store import connect
from app.yaml_config import load_yaml

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
    "hist_corp_actions",
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

    lines.append("\n== Corporate actions ==")
    for kind, n, unknown in con.execute(
        "SELECT kind, count(*), count(*) FILTER (WHERE factor IS NULL AND amount IS NULL) FROM hist_corp_actions GROUP BY 1 ORDER BY 1"
    ).fetchall():
        lines.append(
            f"  {kind:17s} {n:>5}" + (f"  ({unknown} without a factor)" if unknown else "")
        )
    unknown = con.execute(
        "SELECT symbol, ex_date, kind FROM hist_corp_actions WHERE factor IS NULL AND amount IS NULL ORDER BY ex_date LIMIT 10"
    ).fetchall()
    if unknown:
        lines.append(
            "  no factor (returns across these days unused; add the factor in data/corporate_actions.yaml):"
        )
        lines += [f"    {s} {d} {k}" for s, d, k in unknown]
    limit = float(load_yaml("history.yaml")["corporate_actions"]["max_move_without_action_pct"])
    big = big_moves_without_action(con, limit)
    lines.append(f"  one-day moves > {limit:.0f}% with no corporate action recorded: {len(big)}")
    lines += [f"    {s} {d0} -> {d1}: {r:+.1f}%" for s, d0, d1, r in big[:20]]
    if big:
        problems = True
        lines.append(
            "    check each: a missing split/bonus/demerger (add it to data/corporate_actions.yaml) or a real move"
        )

    lines.append("\n== Point-in-time F&O universe ==")
    row = con.execute("SELECT count(DISTINCT symbol), max(date) FROM hist_fno_universe").fetchone()
    if row and row[1]:
        latest = con.execute(
            "SELECT count(*) FROM hist_fno_universe WHERE date = ?", [row[1]]
        ).fetchone()[0]  # type: ignore[index]
        lines.append(
            f"  {row[0]} stocks were in F&O at some point; {latest} on {row[1]}; "
            f"{row[0] - latest} since removed or delisted (kept)"
        )
        for src, n in con.execute(
            "SELECT coalesce(lot_source, 'missing'), count(*) FROM hist_fno_universe GROUP BY 1 ORDER BY 1"
        ).fetchall():
            lines.append(f"  lot sizes {src:9s} {n:>9,} stock-days")
    else:
        lines.append(
            "  empty (load options first): analogues cannot use the point-in-time universe"
        )
        problems = True

    lines.append("\n== Event study ==")
    for timing, n in con.execute(
        'SELECT timing, count(DISTINCT event_id) FROM event_reactions WHERE "window" = 0 GROUP BY 1 ORDER BY 1'
    ).fetchall():
        lines.append(f"  timing {timing:16s} {n:>8,} events")
    stats = con.execute("""
        SELECT count(DISTINCT event_id) FILTER (WHERE confounded),
               count(DISTINCT event_id) FILTER (WHERE weight = 0),
               count(DISTINCT event_id) FILTER (WHERE abnormal_sector IS NOT NULL),
               count(DISTINCT event_id)
        FROM event_reactions""").fetchone()
    if stats and stats[3]:
        lines.append(
            f"  events: {stats[3]:,} | confounded in some window {stats[0]:,} | excluded (weight 0) {stats[1]:,} | "
            f"with sector benchmark {stats[2]:,}"
        )
    return lines, problems


def big_moves_without_action(
    con: duckdb.DuckDBPyConnection, limit_pct: float
) -> list[tuple[Any, ...]]:
    """Close-to-close moves above the limit between consecutive trading days of a stock, with no
    corporate action whose ex-date falls in (previous day, day]."""
    return con.execute(
        """
        WITH r AS (
            SELECT symbol, date, lag(date) OVER w AS prev_date,
                   (close / lag(close) OVER w - 1) * 100 AS ret
            FROM hist_prices WINDOW w AS (PARTITION BY symbol ORDER BY date))
        SELECT symbol, prev_date, date, ret FROM r
        WHERE abs(ret) > ? AND NOT EXISTS (
            SELECT 1 FROM hist_corp_actions c
            WHERE c.symbol = r.symbol AND c.ex_date > r.prev_date AND c.ex_date <= r.date)
        ORDER BY abs(ret) DESC""",
        [limit_pct],
    ).fetchall()


def main() -> int:
    with connect() as con:
        lines, problems = report(con)
    print("\n".join(lines))
    print("\nRESULT:", "CHECK THE ITEMS ABOVE" if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["date"]
