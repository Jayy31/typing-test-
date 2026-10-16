"""Point-in-time F&O universe and lot sizes (no survivorship bias).

`hist_fno_universe` has one row per day per stock that had stock options in that day's F&O
bhavcopy, so stocks later removed from F&O or delisted stay in the history. Lot sizes:
  - 'bhavcopy': from the UDiFF file (July 2024 on), exact for that day;
  - 'inferred': older days (the old format has no lot column) take the next known lot and undo
    split/bonus adjustments in between (lot x factor). NSE's periodic lot revisions before July 2024
    are NOT visible this way: treat 'inferred' lots as approximate;
  - 'override': known values from data/lot_size_overrides.yaml (replace inferred lots only).
Reports must use % of premium as the primary metric when the lot is inferred, and mark rupee
values "approximate (inferred lot size)" (`value_report`).
"""

import bisect
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import yaml

from app.history.corp_actions import DATA, contract_factor, load_actions

OVERRIDES = DATA / "lot_size_overrides.yaml"
EXACT = ("bhavcopy", "override")
APPROX_NOTE = "approximate (inferred lot size)"


def fill_lots(con: duckdb.DuckDBPyConnection) -> int:
    """Mark bhavcopy lots and infer the missing ones. Returns rows inferred."""
    con.execute(
        "UPDATE hist_fno_universe SET lot_source = 'bhavcopy' WHERE lot_size IS NOT NULL AND lot_source IS NULL"
    )
    actions = load_actions(con)
    known: dict[str, list[tuple[date, int]]] = {}
    for sym, d, lot in con.execute(
        "SELECT symbol, date, lot_size FROM hist_fno_universe WHERE lot_source = 'bhavcopy' ORDER BY symbol, date"
    ).fetchall():
        known.setdefault(sym, []).append((d, lot))
    updates = []
    for sym, d in con.execute(
        "SELECT symbol, date FROM hist_fno_universe WHERE lot_size IS NULL OR lot_source = 'inferred'"
    ).fetchall():
        ks = known.get(sym)
        if not ks:
            continue
        i = bisect.bisect_left([k[0] for k in ks], d)
        if i >= len(ks):
            continue
        later_day, later_lot = ks[i]
        f = contract_factor(actions.get(sym, []), d, later_day)  # lot after = lot before / f
        updates.append((max(1, round(later_lot * f)), d, sym))
    con.executemany(
        "UPDATE hist_fno_universe SET lot_size = ?, lot_source = 'inferred' WHERE date = ? AND symbol = ?",
        updates,
    )
    apply_overrides(con)
    return len(updates)


def apply_overrides(con: duckdb.DuckDBPyConnection, path: Path = OVERRIDES) -> int:
    """Owner-known lot sizes replace inferred (or missing) ones; bhavcopy lots are never touched."""
    if not path.exists():
        return 0
    n = 0
    for o in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("overrides") or []:
        start = o["from"] if isinstance(o["from"], date) else date.fromisoformat(str(o["from"]))
        end = o.get("to")
        end = (end if isinstance(end, date) else date.fromisoformat(str(end))) if end else date.max
        rows = con.execute(
            """UPDATE hist_fno_universe SET lot_size = ?, lot_source = 'override'
               WHERE symbol = ? AND date BETWEEN ? AND ? AND coalesce(lot_source, 'inferred') IN ('inferred', 'override')
               RETURNING 1""",
            [int(o["lot_size"]), o["symbol"], start, end],
        ).fetchall()
        n += len(rows)
    return n


@dataclass(frozen=True)
class ValueReport:
    """How a trade/event result must be reported (backtester, analogue option returns)."""

    pct_of_premium: float
    inr: float | None
    lot_source: str | None
    primary: str  # "inr" (exact lot) or "pct" (inferred / unknown lot)
    note: str | None

    def text(self) -> str:
        pct = f"{self.pct_of_premium:+.1f}% of premium"
        if self.inr is None:
            return pct
        if self.primary == "inr":
            return f"Rs {self.inr:+,.0f} ({pct})"
        return f"{pct} (Rs {self.inr:+,.0f}, {self.note})"


def value_report(pct_of_premium: float, inr: float | None, lot_source: str | None) -> ValueReport:
    """% of premium is primary unless the lot size is exact (bhavcopy or owner override)."""
    if lot_source in EXACT:
        return ValueReport(pct_of_premium, inr, lot_source, "inr", None)
    return ValueReport(pct_of_premium, inr, lot_source, "pct", APPROX_NOTE)


def members(con: duckdb.DuckDBPyConnection) -> set[tuple[date, str]]:
    """(date, symbol) pairs in F&O, for fast membership checks."""
    return {(d, s) for d, s in con.execute("SELECT date, symbol FROM hist_fno_universe").fetchall()}


def fno_on(con: duckdb.DuckDBPyConnection, d: date) -> set[str]:
    """F&O stocks as of `d` (last F&O file on or before d)."""
    row = con.execute("SELECT max(date) FROM hist_fno_universe WHERE date <= ?", [d]).fetchone()
    if not row or row[0] is None:
        return set()
    return {
        r[0]
        for r in con.execute(
            "SELECT symbol FROM hist_fno_universe WHERE date = ?", [row[0]]
        ).fetchall()
    }


def lot_on(con: duckdb.DuckDBPyConnection, symbol: str, d: date) -> tuple[int | None, str | None]:
    """(lot size, source) for a stock as of `d`; (None, None) if it was not in F&O then."""
    row = con.execute(
        """SELECT lot_size, lot_source FROM hist_fno_universe WHERE symbol = ?
           AND date = (SELECT max(date) FROM hist_fno_universe WHERE date <= ?)""",
        [symbol, d],
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def all_symbols(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Every stock that was ever in F&O in the history (incl. removed/delisted)."""
    return {r[0] for r in con.execute("SELECT DISTINCT symbol FROM hist_fno_universe").fetchall()}
