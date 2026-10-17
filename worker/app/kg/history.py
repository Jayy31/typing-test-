"""Copy company graph edges into the history store (nightly) for the event study and backtests."""

import duckdb
from sqlalchemy import Engine, select
from sqlalchemy.orm import aliased

from app.db.models import Instrument, KgEdge, KgNode
from app.db.session import session_scope


def sync_kg_edges(con: duckdb.DuckDBPyConnection, engine: Engine | None = None) -> int:
    a, b = aliased(KgNode), aliased(KgNode)
    with session_scope(engine) as s:
        sectors = {i.symbol: i.sector for i in s.scalars(select(Instrument))}
        rows = [
            (
                sa.symbol,
                sb.symbol,
                e.type,
                e.weight,
                sectors.get(sb.symbol or ""),
                e.valid_from,
                e.valid_to,
            )
            for e, sa, sb in s.execute(
                select(KgEdge, a, b).join(a, a.id == KgEdge.src).join(b, b.id == KgEdge.dst)
            )
            if sa.kind == "company" and sb.kind == "company" and sa.symbol and sb.symbol
        ]
    con.execute("DELETE FROM hist_kg_edges")
    if rows:
        con.executemany("INSERT INTO hist_kg_edges VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)
