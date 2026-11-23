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
from app.yaml_config import load_yaml

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


def _cfg() -> dict[str, Any]:
    return dict(load_yaml("history.yaml").get("tagging") or {})


def category(title: str) -> str:
    """NSE announcement category: the text after 'SYMBOL: ' in the stored title."""
    return title.split(":", 1)[1].strip() if ":" in title else title


def skipped_reason(
    title: str, symbol: str | None, fno: set[str], cfg: dict[str, Any]
) -> str | None:
    """Why an item is not sent to the AI (None = tag it). Owner rule: only stocks ever in F&O,
    and no routine filings that never move a price."""
    if cfg.get("fno_only", True) and fno and (symbol or "").upper() not in fno:
        return "not_fno"
    cat = category(title).lower()
    if any(p.lower() in cat for p in cfg.get("skip_categories") or []):
        return "routine"
    return None


def pending(
    con: duckdb.DuckDBPyConnection, counts: dict[str, int] | None = None
) -> list[tuple[str, str, str | None, str]]:
    cfg = _cfg()
    fno = {s.upper() for s in universe.all_symbols(con)}
    out = []
    for nid, title, body, src, sym in con.execute(
        "SELECT news_id, title, body, source, symbol FROM hist_raw_news "
        "WHERE tag_status = 'pending' ORDER BY ts"
    ).fetchall():
        why = skipped_reason(title, sym, fno, cfg)
        if counts is not None:
            counts[why or "to_tag"] = counts.get(why or "to_tag", 0) + 1
        if why is None:
            out.append((nid, title, body, src))
    return out


def estimate(con: duckdb.DuckDBPyConnection) -> CostEstimate:
    return tagger.estimate(len(pending(con)))


def breakdown(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    pending(con, counts)
    return counts


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
            b = breakdown(con)
            print(
                f"pending {sum(b.values()):,}: to tag {b.get('to_tag', 0):,}, skipped "
                f"(not an F&O stock) {b.get('not_fno', 0):,}, skipped (routine filing) "
                f"{b.get('routine', 0):,}"
            )
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
