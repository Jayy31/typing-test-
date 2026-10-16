"""Raw option-quote archive: full-resolution snapshots (every field, incl. bid/ask quantities)
written to Parquet on the VM before Supabase rows are thinned. Partitioned by IST date:
<HISTORY_DATA_DIR>/parquet/option_quotes/date=YYYY-MM-DD/part-<first_id>-<last_id>.parquet
Idempotent: the last archived row id is kept in the config table.
"""

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
from sqlalchemy import Engine, select

from app.db.config_store import get_config, set_config
from app.db.models import OptionQuote
from app.db.session import session_scope
from app.history.store import parquet_dir
from app.timeutil import as_ist
from app.yaml_config import load_yaml

ARCHIVED_KEY = "quotes_archived_upto_id"
COLUMNS = [
    ("id", "BIGINT"),
    ("ts", "TIMESTAMPTZ"),
    ("symbol", "VARCHAR"),
    ("expiry", "DATE"),
    ("strike", "DOUBLE"),
    ("type", "VARCHAR"),
    ("bid", "DOUBLE"),
    ("ask", "DOUBLE"),
    ("ltp", "DOUBLE"),
    ("iv", "DOUBLE"),
    ("oi", "BIGINT"),
    ("volume", "BIGINT"),
    ("spot", "DOUBLE"),
    ("bid_qty", "BIGINT"),
    ("ask_qty", "BIGINT"),
]


def write_parquet(rows: list[tuple[Any, ...]], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()  # in-memory: never touches the history DB's writer lock
    try:
        con.execute(f"CREATE TABLE q ({', '.join(f'{n} {t}' for n, t in COLUMNS)})")
        con.executemany(f"INSERT INTO q VALUES ({', '.join('?' * len(COLUMNS))})", rows)
        tmp = target.with_suffix(".tmp")
        con.execute(f"COPY q TO '{tmp}' (FORMAT PARQUET)")
        tmp.replace(target)  # atomic: a crash never leaves a half file under the final name
    finally:
        con.close()


def archive_option_quotes(engine: Engine | None = None) -> int:
    """Archive rows newer than the last archived id. Returns rows archived."""
    batch = int(load_yaml("options.yaml")["polling"]["archive_batch_rows"])
    with session_scope(engine) as s:
        last = int(get_config(s, ARCHIVED_KEY, 0) or 0)
        quotes = s.scalars(
            select(OptionQuote).where(OptionQuote.id > last).order_by(OptionQuote.id).limit(batch)
        ).all()
        by_day: dict[date, list[tuple[Any, ...]]] = defaultdict(list)
        for q in quotes:
            by_day[as_ist(q.ts).date()].append(
                tuple(getattr(q, name) if name != "ts" else as_ist(q.ts) for name, _ in COLUMNS)
            )
    if not quotes:
        return 0
    base = parquet_dir("option_quotes")
    for day, rows in by_day.items():
        write_parquet(
            rows, base / f"date={day.isoformat()}" / f"part-{rows[0][0]}-{rows[-1][0]}.parquet"
        )
    with session_scope(engine) as s:
        set_config(s, ARCHIVED_KEY, quotes[-1].id)
    return len(quotes)
