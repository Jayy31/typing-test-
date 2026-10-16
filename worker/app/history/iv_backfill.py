"""IV history backfill (DESIGN_V2 R6): daily IV per strike from bhavcopy EOD option closes,
by Black-Scholes inversion, so IV percentile works from day one.

For every day and stock: the two nearest expiries with >= 3 days left, strikes around the day's
ATM (config), IV of each option close given the stock close. ATM IV = mean of the ATM call and
put IV of the nearest such expiry. Incremental: only days not yet in hist_atm_iv.

    python -m app.history.iv_backfill
"""

import logging
import sys
from collections import defaultdict
from datetime import date
from typing import Any

import duckdb
import numpy as np

from app.config import get_settings
from app.history.store import connect
from app.pricing.bs import implied_vol_vec
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)


def backfill_day(con: duckdb.DuckDBPyConnection, d: date) -> int:
    cfg = load_yaml("history.yaml")["iv_backfill"]
    rows = con.execute(
        """
        SELECT o.symbol, o.expiry, o.strike, o.type, o.close, p.close
        FROM hist_options o JOIN hist_prices p ON p.date = o.date AND p.symbol = o.symbol
        WHERE o.date = ? AND o.close >= ? AND o.expiry >= ? + INTERVAL 3 DAY
        ORDER BY o.symbol, o.expiry, o.strike""",
        [d, float(cfg["min_premium"]), d],
    ).fetchall()
    if not rows:
        return 0
    by_sym: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r)
    n_side = int(cfg["max_strikes_each_side"])
    keep: list[tuple[Any, ...]] = []
    for rs in by_sym.values():
        spot = rs[0][5]
        for exp in sorted({r[1] for r in rs})[:2]:
            strikes = sorted({r[2] for r in rs if r[1] == exp})
            i = min(range(len(strikes)), key=lambda k: abs(strikes[k] - spot))
            band = set(strikes[max(0, i - n_side) : i + n_side + 1])
            keep += [r for r in rs if r[1] == exp and r[2] in band]
    if not keep:
        return 0
    arr = np.array([(r[4], r[5], r[2], (r[1] - d).days / 365) for r in keep], dtype=np.float64)
    calls = np.array([r[3] == "CE" for r in keep])
    ivs = implied_vol_vec(
        arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], get_settings().risk_free_rate, calls
    )
    out = [
        (d, r[0], r[1], r[2], r[3], float(iv) * 100, r[5])
        for r, iv in zip(keep, ivs, strict=True)
        if not np.isnan(iv)
    ]
    con.executemany("INSERT OR REPLACE INTO hist_iv VALUES (?, ?, ?, ?, ?, ?, ?)", out)
    # ATM IV per stock: nearest expiry, strike closest to the stock close, mean of CE and PE
    grouped: dict[str, dict[date, list[tuple[float, float, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for _d, sym, exp, k, _typ, iv, spot in out:
        grouped[sym][exp].append((k, iv, spot))
    atm: dict[str, tuple[date, list[float]]] = {}
    for sym, by_exp in grouped.items():
        exp = min(by_exp)
        legs = by_exp[exp]
        nearest = min({k for k, _, _ in legs}, key=lambda x: abs(x - legs[0][2]))
        atm[sym] = (exp, [iv for k, iv, _ in legs if k == nearest])
    con.executemany(
        "INSERT OR REPLACE INTO hist_atm_iv VALUES (?, ?, ?, ?)",
        [(d, s, e, sum(v) / len(v)) for s, (e, v) in atm.items() if v],
    )
    return len(out)


def run(con: duckdb.DuckDBPyConnection | None = None) -> int:
    def body(c: duckdb.DuckDBPyConnection) -> int:
        days = [
            r[0]
            for r in c.execute("""
            SELECT DISTINCT date FROM hist_options
            WHERE date NOT IN (SELECT DISTINCT date FROM hist_atm_iv) ORDER BY date""").fetchall()
        ]
        total = 0
        for i, d in enumerate(days, 1):
            total += backfill_day(c, d)
            if i % 50 == 0:
                log.info("iv backfill %d/%d (%s)", i, len(days), d)
        return total

    if con is not None:
        return body(con)
    with connect(write=True) as c:
        return body(c)


def atm_iv_history(symbol: str, since: date) -> list[float]:
    """Daily ATM IV (%) from the history store; empty if the store is busy or missing."""
    try:
        with connect() as con:
            return [
                r[0]
                for r in con.execute(
                    "SELECT atm_iv FROM hist_atm_iv WHERE symbol = ? AND date >= ? ORDER BY date",
                    [symbol, since],
                ).fetchall()
            ]
    except Exception as exc:  # noqa: BLE001 - history is optional for live percentile
        log.info("IV history unavailable: %s", exc)
        return []


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    print("IV rows:", run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
