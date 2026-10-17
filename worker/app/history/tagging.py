"""Historical batch tagging: LABELS ONLY (DESIGN_V2 R1), same tagger and taxonomy as live.

    python -m app.history.tagging estimate            # count + cost; nothing is sent
    python -m app.history.tagging submit --yes        # owner-approved: submit Message Batches
    python -m app.history.tagging collect             # fetch finished batches -> hist_events

The tagger extracts event type, affected stocks, sentiment and novelty from the text alone; it
is never asked what happened next. Outcomes come only from actual prices (event study).
"""

import argparse
import logging
import sys
from datetime import datetime
from typing import Any

import duckdb

from app import taxonomy
from app.ai import tagger
from app.ai.client import AiClient, CostEstimate
from app.ai.schemas import TaggerOutput
from app.db.models import Instrument
from app.db.session import session_scope
from app.history import universe
from app.history.integrity import LABELS_ONLY
from app.history.store import HIST_EVENTS_INSERT, connect

log = logging.getLogger(__name__)
BATCH_SIZE = 10_000


def _with_history_universe(
    ctx: tagger.TagContext, con: duckdb.DuckDBPyConnection
) -> tagger.TagContext:
    """Allow every stock that was in F&O at any point in the history (incl. stocks since removed
    or delisted), not just today's list: otherwise the tagger drops them (survivorship bias)."""
    return tagger.TagContext(
        sorted(set(ctx.universe) | universe.all_symbols(con)), ctx.sectors, ctx.mapper
    )


def pending(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str, str | None, str]]:
    return [
        (r[0], r[1], r[2], r[3])
        for r in con.execute(
            "SELECT news_id, title, body, source FROM hist_raw_news WHERE tag_status = 'pending' ORDER BY ts"
        ).fetchall()
    ]


def estimate(con: duckdb.DuckDBPyConnection) -> CostEstimate:
    return tagger.estimate(len(pending(con)))


def submit(con: duckdb.DuckDBPyConnection, ai: AiClient) -> list[str]:
    with session_scope() as s:
        ctx = _with_history_universe(tagger.load_context(s), con)
    rows = pending(con)
    batch_ids = []
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i : i + BATCH_SIZE]
        inputs = [tagger.TagInput(nid, title, body, src) for nid, title, body, src in chunk]
        bid = tagger.submit_batch(ai, inputs, ctx)
        con.executemany(
            "UPDATE hist_raw_news SET tag_status = 'submitted', batch_id = ? WHERE news_id = ?",
            [(bid, nid) for nid, *_ in chunk],
        )
        batch_ids.append(bid)
    return batch_ids


def collect(con: duckdb.DuckDBPyConnection, ai: AiClient) -> int:
    """Store labels from finished batches. Returns events written."""
    with session_scope() as s:
        ctx = _with_history_universe(tagger.load_context(s), con)
        sectors = {i.symbol: i.sector for i in s.query(Instrument)}
    written = 0
    batches = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT batch_id FROM hist_raw_news WHERE tag_status = 'submitted'"
        ).fetchall()
    ]
    for bid in batches:
        if not ai.batch_done(bid):
            log.info("batch %s still running", bid)
            continue
        results = tagger.collect_batch(ai, bid, ctx)
        for nid, out in results.items():
            if out is None:
                con.execute(
                    "UPDATE hist_raw_news SET tag_status = 'failed' WHERE news_id = ?", [nid]
                )
                continue
            meta = con.execute(
                "SELECT ts, source, title, url FROM hist_raw_news WHERE news_id = ?", [nid]
            ).fetchone()
            assert meta is not None
            written += write_events(con, nid, meta, out, sectors)
            con.execute("UPDATE hist_raw_news SET tag_status = 'tagged' WHERE news_id = ?", [nid])
    return written


def write_events(
    con: duckdb.DuckDBPyConnection,
    nid: str,
    meta: tuple[Any, ...],
    out: TaggerOutput,
    sectors: dict[str, str],
) -> int:
    ts: datetime = meta[0]
    if not (out.is_actionable and out.impacts):
        return 0
    n = 0
    for k, imp in enumerate(out.impacts):
        con.execute(
            HIST_EVENTS_INSERT,
            [
                f"{nid}:{k}",
                ts.date(),
                ts.time(),
                imp.symbol,
                imp.sector or sectors.get(imp.symbol or ""),
                ",".join(out.themes) or None,
                out.event_type,
                imp.direction,
                meta[1],
                imp.sentiment,
                out.novelty,  # surprise proxy: novelty label (taxonomy has no separate surprise field)
                meta[2],
                meta[3],
                taxonomy.version(),
                f"{tagger.PROMPT_VERSION}:{LABELS_ONLY}",
            ],
        )
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["estimate", "submit", "collect"])
    p.add_argument("--yes", action="store_true", help="confirm the cost estimate was approved")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    with connect(write=True) as con:
        est = estimate(con)
        if a.cmd == "estimate":
            print(est.summary())
            return 0
        ai = AiClient()
        if a.cmd == "submit":
            if not a.yes:
                print(est.summary())
                print("Not submitted. Re-run with --yes once the owner has approved this cost.")
                return 2
            print("submitted batches:", submit(con, ai))
            return 0
        print("events written:", collect(con, ai))
    return 0


if __name__ == "__main__":
    sys.exit(main())
