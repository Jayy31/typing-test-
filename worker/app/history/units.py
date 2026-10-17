"""Detect how each F&O bhavcopy format reports volume and open interest (contracts or shares).

If a column is in shares, its non-zero values are exact multiples of the lot size; in contracts
they almost never are (lot sizes are in the hundreds or thousands). Per format and column: on a
sample of rows with a known lot size > 1 and a non-zero value, >= `shares_min` share of exact
multiples -> shares, <= `contracts_max` -> contracts, otherwise inconclusive (owner alerted, the
config default stays in use). Results are stored in store_meta and used by the backtester.
"""

import logging
from typing import Any

import duckdb

from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
FORMATS = ("fo_old", "fo_udiff")
FIELDS = {"volume": "volume", "oi": "oi"}
SHARES_MIN, CONTRACTS_MAX, MIN_SAMPLE = 0.97, 0.20, 200


def _range(fmt: str) -> tuple[str, list[Any]]:
    src = load_yaml("history.yaml")["sources"]
    if fmt == "fo_old":
        return "o.date <= ?", [src["fo_old"]["until"]]
    return "o.date >= ?", [src["fo_udiff"]["from"]]


def detect(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    """{format: {field: {"unit": shares|contracts|None, "share_of_multiples": x, "sample": n}}}."""
    out: dict[str, dict[str, Any]] = {}
    for fmt in FORMATS:
        where, params = _range(fmt)
        out[fmt] = {}
        for field, col in FIELDS.items():
            row = con.execute(
                f"""SELECT count(*), sum(CASE WHEN o.{col} % u.lot_size = 0 THEN 1 ELSE 0 END)
                    FROM (SELECT * FROM hist_options o WHERE {where} AND o.{col} > 0 USING SAMPLE 20000 ROWS) o
                    JOIN hist_fno_universe u ON u.date = o.date AND u.symbol = o.symbol
                    WHERE u.lot_size > 1""",
                params,
            ).fetchone()
            n, mult = (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)
            share = mult / n if n else None
            unit = None
            if n >= MIN_SAMPLE and share is not None:
                unit = (
                    "shares"
                    if share >= SHARES_MIN
                    else "contracts"
                    if share <= CONTRACTS_MAX
                    else None
                )
            out[fmt][field] = {
                "unit": unit,
                "share_of_multiples": None if share is None else round(share, 3),
                "sample": n,
            }
    return out


def store(con: duckdb.DuckDBPyConnection, result: dict[str, dict[str, Any]]) -> list[str]:
    """Save detected units; return problems to alert about (inconclusive with enough data)."""
    problems = []
    for fmt, fields in result.items():
        for field, r in fields.items():
            if r["unit"]:
                con.execute(
                    "INSERT OR REPLACE INTO store_meta VALUES (?, ?)",
                    [f"units:{fmt}:{field}", r["unit"]],
                )
            elif r["sample"] >= MIN_SAMPLE:
                problems.append(
                    f"{fmt} {field}: {r['share_of_multiples']:.0%} of {r['sample']} values are exact multiples of the lot "
                    f"size (shares needs >= {SHARES_MIN:.0%}, contracts <= {CONTRACTS_MAX:.0%}); keeping the config default"
                )
    return problems


def stored(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, str], str]:
    rows = con.execute("SELECT key, value FROM store_meta WHERE key LIKE 'units:%'").fetchall()
    return {(k.split(":")[1], k.split(":")[2]): v for k, v in rows}


def run_detection(con: duckdb.DuckDBPyConnection) -> list[str]:
    res = detect(con)
    problems = store(con, res)
    log.info("bhavcopy units: %s", res)
    return problems
