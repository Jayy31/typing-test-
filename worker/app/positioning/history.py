"""Positioning history (feat.positioning): states per stock per day from the bhavcopy history
(futures, options, IV, delivery %) and their base rates (forward abnormal return vs Nifty).

`build` is incremental (days after the last one stored); `compute_stats(before=)` uses only days
whose forward window ended before `before`, so the backtester sees no look-ahead.
"""

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import duckdb
import numpy as np

from app.positioning.features import DayInput, OptRow, cfg, compute

log = logging.getLogger(__name__)


def _days(con: duckdb.DuckDBPyConnection, start: date | None) -> list[date]:
    q = (
        "SELECT DISTINCT date FROM hist_futures"
        + (" WHERE date > ?" if start else "")
        + " ORDER BY 1"
    )
    return [r[0] for r in con.execute(q, [start] if start else []).fetchall()]


def day_inputs(con: duckdb.DuckDBPyConnection, d: date, prev: date | None) -> dict[str, DayInput]:
    """Every stock's inputs for one day (prev = previous trading day, for changes)."""
    c = cfg()
    look = int(c["unusual_activity"]["lookback_days"])
    futs: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for sym, exp, close, oi, chg in con.execute(
        "SELECT symbol, expiry, coalesce(close, settle), oi, chg_oi FROM hist_futures WHERE date = ? ORDER BY symbol, expiry",
        [d],
    ).fetchall():
        futs[sym].append((exp, close, oi, chg))
    prev_fut = (
        dict(
            con.execute(
                "SELECT symbol || '|' || CAST(expiry AS VARCHAR), coalesce(close, settle) FROM hist_futures WHERE date = ?",
                [prev],
            ).fetchall()
        )
        if prev
        else {}
    )
    prices = {
        s: (c_, dp)
        for s, c_, dp in con.execute(
            "SELECT symbol, close, delivery_pct FROM hist_prices WHERE date = ?", [d]
        ).fetchall()
    }
    prev_close = (
        dict(con.execute("SELECT symbol, close FROM hist_prices WHERE date = ?", [prev]).fetchall())
        if prev
        else {}
    )
    deliv_avg = dict(
        con.execute(
            """SELECT symbol, avg(delivery_pct) FROM (
                 SELECT symbol, delivery_pct, row_number() OVER (PARTITION BY symbol ORDER BY date DESC) rn
                 FROM hist_prices WHERE date < ? AND delivery_pct IS NOT NULL) WHERE rn <= ? GROUP BY symbol""",
            [d, int(c["delivery"]["lookback_days"])],
        ).fetchall()
    )
    # near-month option rows with IV and 20-day per-contract averages
    opts: dict[str, list[OptRow]] = defaultdict(list)
    near = {s: rows[0][0] for s, rows in futs.items()}
    nxt = {s: rows[1][0] for s, rows in futs.items() if len(rows) > 1}
    rows = con.execute(
        f"""
        WITH h AS (
          SELECT date, symbol, expiry, strike, type, oi, chg_oi, volume,
                 avg(volume) OVER w AS avg_vol, avg(abs(chg_oi)) OVER w AS avg_chg
          FROM hist_options WHERE date <= ? AND date >= ? - INTERVAL {look * 2} DAY
          WINDOW w AS (PARTITION BY symbol, expiry, strike, type ORDER BY date
                       ROWS BETWEEN {look} PRECEDING AND 1 PRECEDING))
        SELECT h.symbol, h.expiry, h.strike, h.type, h.oi, h.chg_oi, h.volume, i.iv, h.avg_vol, h.avg_chg
        FROM h LEFT JOIN hist_iv i ON i.date = h.date AND i.symbol = h.symbol AND i.expiry = h.expiry
             AND i.strike = h.strike AND i.type = h.type
        WHERE h.date = ?""",
        [d, d, d],
    ).fetchall()
    next_rows: dict[str, list[OptRow]] = defaultdict(list)
    for sym, exp, k, typ, oi, chg, vol, iv, av, ac in rows:
        r = OptRow(float(k), typ, oi, chg, vol, iv, av, ac)
        if near.get(sym) == exp:
            opts[sym].append(r)
        elif nxt.get(sym) == exp:
            next_rows[sym].append(r)
    prev_pcr: dict[str, float] = {}
    if prev:
        for sym, puts, calls in con.execute(
            """SELECT o.symbol, sum(CASE WHEN type = 'PE' THEN oi ELSE 0 END), sum(CASE WHEN type = 'CE' THEN oi ELSE 0 END)
               FROM hist_options o JOIN (SELECT symbol, min(expiry) e FROM hist_futures WHERE date = ? GROUP BY 1) f
                 ON f.symbol = o.symbol AND f.e = o.expiry
               WHERE o.date = ? GROUP BY 1""",
            [prev, prev],
        ).fetchall():
            if calls:
                prev_pcr[sym] = float(puts) / float(calls)
    from app.positioning.features import atm_iv

    out: dict[str, DayInput] = {}
    for sym, fl in futs.items():
        exp, close, oi, chg = fl[0]
        spot, dp = prices.get(sym, (None, None))
        out[sym] = DayInput(
            spot=spot,
            prev_spot=prev_close.get(sym),
            fut_close=close,
            fut_prev_close=prev_fut.get(f"{sym}|{exp}"),
            fut_oi=oi,
            fut_chg_oi=chg,
            next_fut_oi=fl[1][2] if len(fl) > 1 else None,
            days_to_expiry=_trading_days(con, d, exp),
            near=opts.get(sym, []),
            next_atm_iv=atm_iv(next_rows.get(sym, []), spot),
            prev_pcr=prev_pcr.get(sym),
            delivery_pct=dp,
            delivery_avg=deliv_avg.get(sym),
        )
    return out


_CAL: list[date] = []


def _trading_days(con: duckdb.DuckDBPyConnection, d: date, exp: date) -> int:
    global _CAL
    if not _CAL or _CAL[-1] < exp:
        _CAL = [
            r[0] for r in con.execute("SELECT DISTINCT date FROM hist_prices ORDER BY 1").fetchall()
        ]
    n = sum(1 for x in _CAL if d < x <= exp)
    if exp > (_CAL[-1] if _CAL else d):  # expiry beyond loaded history: count weekdays
        from datetime import timedelta

        x = max(_CAL[-1] if _CAL else d, d)
        while x < exp:
            x += timedelta(days=1)
            n += x.weekday() < 5
    return n


def build(con: duckdb.DuckDBPyConnection) -> int:
    """Add states for every futures day not yet processed. Idempotent."""
    last = con.execute("SELECT max(date) FROM hist_positioning").fetchone()
    days = _days(con, last[0] if last else None)
    all_days = [
        r[0] for r in con.execute("SELECT DISTINCT date FROM hist_futures ORDER BY 1").fetchall()
    ]
    prev_of = {d: all_days[i - 1] if i else None for i, d in enumerate(all_days)}
    n = 0
    for d in days:
        rows = []
        for sym, x in day_inputs(con, d, prev_of.get(d)).items():
            for feat, (state, value) in compute(x).items():
                rows.append((d, sym, feat, state, value))
        con.executemany("INSERT OR REPLACE INTO hist_positioning VALUES (?, ?, ?, ?, ?)", rows)
        n += len(rows)
    return n


def stat_rows(
    con: duckdb.DuckDBPyConnection, before: date | None = None
) -> dict[tuple[str, str, int], list[float]]:
    """Forward abnormal returns (% vs Nifty 50) after each day in each state."""
    w = int(cfg()["evidence"]["window"])
    cal = [r[0] for r in con.execute("SELECT DISTINCT date FROM hist_prices ORDER BY 1").fetchall()]
    pos = {d: i for i, d in enumerate(cal)}
    nifty = dict(
        con.execute(
            "SELECT date, close FROM hist_index WHERE lower(index_name) = 'nifty 50'"
        ).fetchall()
    )
    closes: dict[str, dict[date, float]] = defaultdict(dict)
    for s, d, c in con.execute("SELECT symbol, date, close FROM hist_prices").fetchall():
        if c:
            closes[s][d] = float(c)
    out: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for d, sym, feat, state in con.execute(
        "SELECT date, symbol, feature, state FROM hist_positioning"
    ).fetchall():
        i = pos.get(d)
        if i is None or i + w >= len(cal):
            continue
        end = cal[i + w]
        if before is not None and end >= before:
            continue
        a, b = closes[sym].get(d), closes[sym].get(end)
        na, nb = nifty.get(d), nifty.get(end)
        if not (a and b and na and nb):
            continue
        out[(feat, state, w)].append(((b / a) - (nb / na)) * 100)
    return out


def summarize(
    rows: dict[tuple[str, str, int], list[float]],
) -> dict[tuple[str, str, int], tuple[int, float, float, float]]:
    """(n, mean %, share of up moves, std) per (feature, state, window)."""
    out = {}
    for k, v in rows.items():
        a = np.array(v)
        out[k] = (len(a), float(a.mean()), float((a > 0).mean()), float(a.std()))
    return out


def compute_stats(con: duckdb.DuckDBPyConnection) -> int:
    stats = summarize(stat_rows(con))
    con.execute("DELETE FROM positioning_stats")
    now = datetime.now().replace(microsecond=0)
    con.executemany(
        "INSERT INTO positioning_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(*k, *v, now) for k, v in stats.items()],
    )
    return len(stats)


def nightly() -> int:
    from app.history.store import connect

    with connect(write=True) as con:
        n = build(con)
        compute_stats(con)
    log.info("positioning history: %d new states", n)
    return n
