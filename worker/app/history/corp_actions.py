"""Corporate actions for the history (splits, bonus, demergers, special dividends).

Each action has a price `factor`: the multiplier applied to prices BEFORE the ex-date so they are
comparable with prices after it (split 10 -> 2: 0.2; bonus 1:1: 0.5). Special dividends store the
amount; the factor (prev_close - amount) / prev_close is worked out from prices. Demergers have no
reliable factor in the NSE text: factor NULL = "unknown" (that day's return is not used) unless
the owner records it in data/corporate_actions.yaml.

The same factor adjusts option contracts on the ex-date (NSE adjusts strikes by the factor and
lot sizes by its inverse), see `contract_factor`.

    python -m app.history.corp_actions --from 2021-10-01 --to 2026-10-16
"""

import argparse
import bisect
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import yaml

from app.collectors.nse import classify_action, nse_get
from app.config import IST
from app.history.news_history import month_chunks
from app.history.store import connect
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
KINDS = ("split", "bonus", "demerger", "special_dividend")
DATA = Path(__file__).resolve().parents[3] / "data"

_SPLIT = re.compile(
    r"from\s*(?:rs\.?|re\.?|inr)?\s*([\d.]+).*?to\s*(?:rs\.?|re\.?|inr)?\s*([\d.]+)", re.I
)
_BONUS = re.compile(r"(\d+)\s*:\s*(\d+)")
_AMOUNT = re.compile(r"(?:rs\.?|re\.?|inr)\s*([\d.]+)", re.I)


@dataclass(frozen=True)
class Action:
    symbol: str
    ex_date: date
    kind: str
    factor: float | None  # price multiplier for dates before ex_date; None = unknown
    amount: float | None = None  # special dividend per share


def parse_subject(subject: str) -> tuple[str | None, float | None, float | None]:
    """NSE subject text -> (kind, factor, amount). Kinds outside KINDS -> (None, ...)."""
    kind = classify_action(subject)
    if kind not in KINDS:
        return None, None, None
    if kind == "split":
        m = _SPLIT.search(subject)
        if m and float(m.group(1)) > 0:
            return kind, float(m.group(2)) / float(m.group(1)), None
        return kind, None, None
    if kind == "bonus":  # "Bonus 1:2" = 1 new share for every 2 held -> price x 2/3
        m = _BONUS.search(subject)
        if m and int(m.group(2)) > 0:
            new, held = int(m.group(1)), int(m.group(2))
            return kind, held / (new + held), None
        return kind, None, None
    if kind == "special_dividend":
        m = _AMOUNT.search(subject)
        return kind, None, float(m.group(1)) if m else None
    return kind, None, None  # demerger


def parse_rows(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    out = []
    for r in rows:
        subject = str(r.get("subject", "") or "")
        kind, factor, amount = parse_subject(subject)
        sym = str(r.get("symbol", "") or "").strip()
        try:
            ex = datetime.strptime(str(r.get("exDate", "")).strip(), "%d-%b-%Y").date()
        except ValueError:
            continue
        if kind and sym:
            out.append((sym, ex, kind, subject[:300], factor, amount, "nse"))
    return out


def store(con: duckdb.DuckDBPyConnection, rows: list[tuple[Any, ...]]) -> int:
    # never overwrite an owner-verified (manual) row with the NSE text parse
    con.executemany(
        """INSERT INTO hist_corp_actions VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT DO UPDATE SET subject = excluded.subject, factor = excluded.factor,
             amount = excluded.amount, source = excluded.source
           WHERE hist_corp_actions.source <> 'manual'""",
        rows,
    )
    return len(rows)


def fetch(start: date, end: date, con: duckdb.DuckDBPyConnection) -> int:
    path = load_yaml("history.yaml")["sources"]["corporate_actions"]["path"]
    n = 0
    for lo, hi in month_chunks(start, end):
        key = f"corp_actions:{lo.isoformat()}"
        if con.execute("SELECT 1 FROM store_meta WHERE key = ?", [key]).fetchone():
            continue
        try:
            raw = nse_get(
                path.format(
                    from_ddmmyyyy=lo.strftime("%d-%m-%Y"), to_ddmmyyyy=hi.strftime("%d-%m-%Y")
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("corporate actions %s..%s failed: %s", lo, hi, exc)
            continue
        rows = parse_rows(raw if isinstance(raw, list) else raw.get("data", []))
        n += store(con, rows)
        if hi < datetime.now(IST).date() - timedelta(days=3):
            con.execute("INSERT OR REPLACE INTO store_meta VALUES (?, ?)", [key, str(len(rows))])
        time.sleep(float(load_yaml("history.yaml")["http"]["min_interval_s"]))
    return n


def load_manual(con: duckdb.DuckDBPyConnection, path: Path | None = None) -> int:
    """Owner-verified actions: {symbol, ex_date, kind, factor?, amount?, note?}. Wins over NSE text."""
    path = path or DATA / load_yaml("history.yaml")["corporate_actions"]["manual_file"]
    if not path.exists():
        return 0
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = []
    for a in data.get("actions") or []:
        ex = (
            a["ex_date"]
            if isinstance(a["ex_date"], date)
            else date.fromisoformat(str(a["ex_date"]))
        )
        rows.append(
            (a["symbol"], ex, a["kind"], a.get("note"), a.get("factor"), a.get("amount"), "manual")
        )
    con.executemany("INSERT OR REPLACE INTO hist_corp_actions VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def load_actions(con: duckdb.DuckDBPyConnection) -> dict[str, list[Action]]:
    out: dict[str, list[Action]] = {}
    for sym, ex, kind, factor, amount in con.execute(
        "SELECT symbol, ex_date, kind, factor, amount FROM hist_corp_actions ORDER BY symbol, ex_date"
    ).fetchall():
        out.setdefault(sym, []).append(Action(sym, ex, kind, factor, amount))
    return out


def resolve_factor(a: Action, dates: list[date], close: list[float]) -> float | None:
    """Price factor, computing special-dividend factors from the close before the ex-date."""
    if a.factor is not None:
        return a.factor
    if a.kind == "special_dividend" and a.amount:
        i = bisect.bisect_left(dates, a.ex_date)
        if i > 0 and close[i - 1] > a.amount:
            return (close[i - 1] - a.amount) / close[i - 1]
    return None


def adjust(
    dates: list[date], values: list[list[float]], actions: list[Action]
) -> tuple[list[list[float]], set[int]]:
    """Back-adjust price lists (close/high/low, aligned with `dates`) for every known factor.
    Returns adjusted lists and the indexes of ex-dates with an UNKNOWN factor (returns across
    those days must not be used)."""
    close = values[0]
    mult = [1.0] * len(dates)
    breaks: set[int] = set()
    for a in actions:
        i = bisect.bisect_left(dates, a.ex_date)
        if i == 0 or i >= len(dates):
            continue
        f = resolve_factor(a, dates, close)
        if f is None or f <= 0:
            breaks.add(i)
            continue
        for k in range(i):
            mult[k] *= f
    return [[v * m for v, m in zip(vals, mult, strict=True)] for vals in values], breaks


def contract_factor(actions: list[Action], after: date, upto: date) -> float:
    """Product of split/bonus factors with ex-date in (after, upto]: an option struck at K before
    is struck at K x factor after, with lot / factor (NSE contract adjustment)."""
    f = 1.0
    for a in actions:
        if after < a.ex_date <= upto and a.kind in ("split", "bonus") and a.factor:
            f *= a.factor
    return f


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start", type=date.fromisoformat)
    p.add_argument("--to", dest="end", type=date.fromisoformat)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    end = a.end or datetime.now(IST).date()
    start = a.start or end.replace(year=end.year - int(load_yaml("history.yaml")["years"]))
    with connect(write=True) as con:
        print("actions:", fetch(start, end, con), "manual:", load_manual(con))
    return 0


if __name__ == "__main__":
    sys.exit(main())
