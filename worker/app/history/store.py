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

SCHEMA_VERSION = 5

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
    # abnormal_ret = primary (vs sector index, else Nifty 50); abnormal_mkt / abnormal_sector both kept
    """CREATE TABLE IF NOT EXISTS event_reactions (
        event_id VARCHAR, symbol VARCHAR, "window" INTEGER, reaction_day DATE, timing VARCHAR,
        raw_ret DOUBLE, abnormal_ret DOUBLE, abnormal_mkt DOUBLE, abnormal_sector DOUBLE,
        benchmark VARCHAR, mfe DOUBLE, mae DOUBLE, atm_ce_ret DOUBLE, atm_pe_ret DOUBLE,
        confounded BOOLEAN, weight DOUBLE,
        PRIMARY KEY (event_id, symbol, "window"))""",
    """CREATE TABLE IF NOT EXISTS analogue_stats (
        event_type VARCHAR, sector VARCHAR, symbol VARCHAR, regime VARCHAR, "window" INTEGER,
        n INTEGER, n_eff DOUBLE, mean_abn_ret DOUBLE, median DOUBLE, hit_rate DOUBLE, std DOUBLE,
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
    # v3: corporate actions (price factor = multiplier for prices BEFORE the ex-date; NULL = unknown)
    """CREATE TABLE IF NOT EXISTS hist_corp_actions (
        symbol VARCHAR, ex_date DATE, kind VARCHAR, subject VARCHAR, factor DOUBLE, amount DOUBLE,
        source VARCHAR, PRIMARY KEY (symbol, ex_date, kind))""",
    "ALTER TABLE hist_fno_universe ADD COLUMN IF NOT EXISTS lot_source VARCHAR",
    # v4 (Phase 7): structured events keep the date the thing HAPPENED in event_date; `date`/`time`
    # are always the public disclosure time, which is all the event study and backtests may use.
    "ALTER TABLE hist_events ADD COLUMN IF NOT EXISTS event_date DATE",
    # v5 (Phase 8): company graph edges as of each date (copied nightly from Postgres kg_edges)
    """CREATE TABLE IF NOT EXISTS hist_kg_edges (
        src VARCHAR, dst VARCHAR, type VARCHAR, weight DOUBLE, dst_sector VARCHAR,
        valid_from DATE, valid_to DATE, PRIMARY KEY (src, dst, type))""",
]
# Positional inserts into hist_events (the v4 event_date column is filled only for structured events)
HIST_EVENTS_INSERT = (
    "INSERT OR REPLACE INTO hist_events (event_id, date, time, symbol, sector, theme, event_type, "
    "direction, source, sentiment, surprise, title, url, taxonomy_ver, prompt_ver) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
# Derived tables rebuilt by the event study; dropped when their columns change.
_REBUILD = {3: ["event_reactions", "analogue_stats"]}


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
    con.execute("CREATE TABLE IF NOT EXISTS store_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    row = con.execute("SELECT value FROM store_meta WHERE key = 'schema_version'").fetchone()
    old = int(row[0]) if row else SCHEMA_VERSION
    for ver, tables in _REBUILD.items():
        if old < ver:
            for tbl in tables:
                con.execute(f"DROP TABLE IF EXISTS {tbl}")
    for stmt in DDL:
        con.execute(stmt)
    con.execute(
        "INSERT OR REPLACE INTO store_meta VALUES ('schema_version', ?)", [str(SCHEMA_VERSION)]
    )


def _needs_upgrade(path: Path) -> bool:
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        return False  # locked by a writer
    try:
        row = con.execute("SELECT value FROM store_meta WHERE key = 'schema_version'").fetchone()
    except duckdb.Error:
        return True
    finally:
        con.close()
    return row is None or int(row[0]) < SCHEMA_VERSION


@contextmanager
def connect(path: Path | None = None, write: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not write:
        if not path.exists() or _needs_upgrade(path):
            try:
                with connect(path, write=True):  # create / upgrade the schema
                    pass
            except StoreBusyError:
                pass  # a writer is running; it upgrades the schema itself
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
