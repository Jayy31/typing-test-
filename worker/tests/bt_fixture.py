"""Synthetic history store for backtester tests."""

from datetime import date, timedelta

import duckdb

from app.history.store import init_schema
from app.pricing.bs import price

START = date(2023, 1, 2)


def calendar(n: int = 380) -> list[date]:
    out, d = [], START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def build(
    event_days: tuple[int, ...] = (300,),
    jump: float = 0.12,
    symbols: tuple[str, ...] = ("ABC",),
    zero_volume_days: tuple[int, ...] = (),
    no_settle: bool = False,
) -> tuple[duckdb.DuckDBPyConnection, list[date]]:
    con = duckdb.connect()
    init_schema(con)
    cal = calendar()
    expiries = [cal[i] for i in range(19, len(cal), 21)] + [cal[-1] + timedelta(days=30)]
    idx: list[tuple] = []
    prices: list[tuple] = []
    uni: list[tuple] = []
    opts: list[tuple] = []
    for i, d in enumerate(cal):
        idx += [(d, "Nifty 50", 18000.0 + i), (d, "India VIX", 15.0), (d, "Nifty IT", 30000.0 + i)]
    for sym in symbols:
        spot = 1000.0
        for i, d in enumerate(cal):
            if any(i == e + 1 for e in event_days):
                spot *= 1 + jump / 2
            if any(e + 2 <= i <= e + 3 for e in event_days):
                spot *= 1 + jump / 4
            prices.append((d, sym, spot, spot * 1.01, spot * 0.99, spot, 1000000, 50.0))
            uni.append((d, sym, 100, "bhavcopy"))
            for exp in [e for e in expiries if e > d][:2]:
                t = (exp - d).days / 365
                atm = round(spot / 20) * 20
                for k in range(int(atm) - 100, int(atm) + 120, 20):
                    for typ in ("CE", "PE"):
                        px = float(price(spot, k, t, 0.065, 0.30, typ))
                        if px < 0.5:
                            continue
                        vol = 0 if i in zero_volume_days else 5000
                        settle = None if no_settle else px
                        opts.append(
                            (
                                d,
                                sym,
                                exp,
                                float(k),
                                typ,
                                px,
                                px * 1.02,
                                px * 0.98,
                                px,
                                settle,
                                2000000,
                                0,
                                vol,
                            )
                        )
    _bulk(con, "hist_index", idx)
    _bulk(con, "hist_prices", prices)
    _bulk(con, "hist_fno_universe", uni)
    _bulk(con, "hist_options", opts)
    return con, cal


def _bulk(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple]) -> None:
    """Fast load through a temporary CSV (DuckDB executemany is slow row by row)."""
    import csv
    import os
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])
        name = f.name
    try:
        con.execute(f"COPY {table} FROM '{name}' (HEADER false, NULLSTR '')")
    finally:
        os.unlink(name)


def add_event(
    con: duckdb.DuckDBPyConnection,
    eid: str,
    d: date,
    sym: str = "ABC",
    etype: str = "results",
    direction: str = "bullish",
    hour: int = 10,
) -> None:
    from datetime import time

    con.execute(
        "INSERT INTO hist_events VALUES (?, ?, ?, ?, 'IT', NULL, ?, ?, 's', 0.6, 0.6, 'title', 'u', 'tx1', 'p')",
        [eid, d, time(hour, 0), sym, etype, direction],
    )


def add_training_reactions(
    con: duckdb.DuckDBPyConnection,
    cal: list[date],
    n: int = 30,
    abn: float = 8.0,
    sym: str = "ABC",
    until: int = 200,
) -> None:
    """Past analogue outcomes (as the event study would store them) before day `until`."""
    for k in range(n):
        d = cal[10 + k * (until - 30) // n]
        eid = f"past{sym}{k}"
        add_event(con, eid, d, sym)
        for w in (0, 1, 3, 5, 10):
            con.execute(
                "INSERT INTO event_reactions VALUES (?, ?, ?, ?, 'intraday', ?, ?, ?, ?, 'nifty it', NULL, NULL, NULL, NULL, false, 1.0)",
                [eid, sym, w, d, abn, abn, abn, abn],
            )
