"""History store (DESIGN_V2 section 1): DuckDB file + Parquet files on the worker VM.

Supabase keeps live/operational data only; multi-year history lives here.
DuckDB allows ONE writer process: `connect(write=True)` takes an exclusive file lock, so a
backfill and the backtester can never write at the same time (the second one fails fast).
"""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from app.config import get_settings

SCHEMA_VERSION = 2

DDL = [
    """CREATE TABLE IF NOT EXISTS hist_prices (
        date DATE, symbol VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
        volume BIGINT, delivery_pct DOUBLE, PRIMARY KEY (date, symbol))""",
    """CREATE TABLE IF NOT EXISTS hist_options (
        date DATE, symbol VARCHAR, expiry DATE, strike DOUBLE, type VARCHAR,
        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, settle DOUBLE,
        oi BIGINT, chg_oi BIGINT, volume BIGINT,
        PRIMARY KEY (date, symbol, expiry, strike, type))""",
    """CREATE TABLE IF NOT EXISTS hist_index (
        date DATE, index_name VARCHAR, close DOUBLE, PRIMARY KEY (date, index_name))""",
    """CREATE TABLE IF NOT EXISTS hist_fno_universe (
        date DATE, symbol VARCHAR, lot_size INTEGER, PRIMARY KEY (date, symbol))""",
    """CREATE TABLE IF NOT EXISTS hist_events (
        event_id VARCHAR PRIMARY KEY, date DATE, time TIME, symbol VARCHAR, sector VARCHAR,
        theme VARCHAR, event_type VARCHAR, direction VARCHAR, source VARCHAR,
        sentiment DOUBLE, surprise DOUBLE, title VARCHAR, url VARCHAR,
        taxonomy_ver VARCHAR, prompt_ver VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS event_reactions (
        event_id VARCHAR, symbol VARCHAR, "window" INTEGER, raw_ret DOUBLE, abnormal_ret DOUBLE,
        mfe DOUBLE, mae DOUBLE, atm_ce_ret DOUBLE, atm_pe_ret DOUBLE,
        PRIMARY KEY (event_id, symbol, "window"))""",
    """CREATE TABLE IF NOT EXISTS analogue_stats (
        event_type VARCHAR, sector VARCHAR, symbol VARCHAR, regime VARCHAR, "window" INTEGER,
        n INTEGER, mean_abn_ret DOUBLE, median DOUBLE, hit_rate DOUBLE, std DOUBLE,
        p25 DOUBLE, p75 DOUBLE, computed_at TIMESTAMP)""",
    "CREATE TABLE IF NOT EXISTS store_meta (key VARCHAR PRIMARY KEY, value VARCHAR)",
    # Phase 4
    """CREATE TABLE IF NOT EXISTS loaded_files (
        kind VARCHAR, date DATE, status VARCHAR, rows INTEGER, loaded_at TIMESTAMP,
        PRIMARY KEY (kind, date))""",
    """CREATE TABLE IF NOT EXISTS hist_raw_news (
        news_id VARCHAR PRIMARY KEY, ts TIMESTAMP, symbol VARCHAR, title VARCHAR, body VARCHAR,
        url VARCHAR, source VARCHAR, tag_status VARCHAR DEFAULT 'pending', batch_id VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS hist_iv (
        date DATE, symbol VARCHAR, expiry DATE, strike DOUBLE, type VARCHAR, iv DOUBLE,
        spot DOUBLE, PRIMARY KEY (date, symbol, expiry, strike, type))""",
    """CREATE TABLE IF NOT EXISTS hist_atm_iv (
        date DATE, symbol VARCHAR, expiry DATE, atm_iv DOUBLE, PRIMARY KEY (date, symbol))""",
]


class StoreBusyError(RuntimeError):
    """Another process holds the history store's write lock."""


def db_path() -> Path:
    return Path(get_settings().duckdb_path)


def parquet_dir(kind: str) -> Path:
    """Raw downloads as Parquet, e.g. <HISTORY_DATA_DIR>/parquet/fo_bhavcopy/2024/..."""
    p = Path(get_settings().history_data_dir) / "parquet" / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    for stmt in DDL:
        con.execute(stmt)
    con.execute(
        "INSERT OR REPLACE INTO store_meta VALUES ('schema_version', ?)", [str(SCHEMA_VERSION)]
    )


@contextmanager
def connect(path: Path | None = None, write: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not write:
        if not path.exists():
            with connect(path, write=True):
                pass
        con = duckdb.connect(str(path), read_only=True)
        try:
            yield con
        finally:
            con.close()
        return
    lock_file = open(f"{path}.lock", "w")  # noqa: SIM115 - held for the connection's lifetime
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise StoreBusyError(f"history store {path} is being written by another process") from None
    con = duckdb.connect(str(path))
    try:
        init_schema(con)
        yield con
    finally:
        con.close()
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
