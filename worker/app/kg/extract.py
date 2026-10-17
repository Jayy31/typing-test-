"""Company links from text (cheap model, prompt kg_links_v1), mapped to stocks with the
confidence-scored name matcher. Edges are dated by the item's publication (point-in-time).

Live: recent tagged news / announcements naming at least one F&O stock, capped per run and day.
History (one-off, owner approval required):
    python -m app.kg.extract estimate          # cost estimate only
    python -m app.kg.extract submit --yes      # after the owner approved the estimate
    python -m app.kg.extract collect           # store finished batches as edges
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Engine, select

from app.ai.client import AiClient, AiOutputError, BudgetExceededError, CostEstimate, estimate_cost
from app.config import IST
from app.db.config_store import get_config, set_config
from app.db.models import Instrument, NewsItem, NewsTag
from app.db.session import session_scope
from app.deep import matching
from app.kg import graph
from app.processing.entity_map import InstrumentInfo
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPT_VERSION = "kg_links_v1"
SYSTEM = (
    Path(__file__).resolve().parents[1] / "ai" / "prompts" / f"{PROMPT_VERSION}.md"
).read_text(encoding="utf-8")
SEEN = "kg.extract.seen"
BATCHES = "kg.extract.batches"


class Link(BaseModel):
    a: str
    b: str
    type: Literal["supplier_of", "customer_of", "competitor_of", "same_group"]
    exposure_kind: Literal["revenue_share", "input_cost_share", "stake"] | None
    exposure_pct: float | None = Field(default=None, ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    quote: str


class Links(BaseModel):
    links: list[Link] = Field(max_length=5)


def cfg() -> dict:  # type: ignore[type-arg]
    return dict(load_yaml("kg.yaml")["extract"])


def _instruments(engine: Engine | None) -> list[InstrumentInfo]:
    with session_scope(engine) as s:
        return [
            InstrumentInfo(i.symbol, i.name, i.sector, tuple(i.aliases or []))
            for i in s.scalars(select(Instrument))
            if i.is_fno
        ]


def store_links(
    engine: Engine | None,
    out: Links,
    published: date,
    news_id: int | None,
    insts: list[InstrumentInfo],
) -> int:
    """Both names must map to a stock with confidence >= the regulator threshold; links below
    `extract.min_confidence` are ignored. Each link is stored in both directions."""
    th, min_c = matching.threshold(), float(cfg()["min_confidence"])
    added = 0
    with session_scope(engine) as s:
        for link in out.links:
            if link.confidence < min_c:
                continue
            ma, mb = matching.match(link.a, insts), matching.match(link.b, insts)
            if not ma or not mb or ma[0].score < th or mb[0].score < th:
                continue
            a, b = ma[0].symbol, mb[0].symbol
            for src, dst, typ in ((a, b, link.type), (b, a, graph.REVERSE[link.type])):
                # exposure is stated from a's side; keep it on the a -> b edge only
                fwd = src == a
                added += graph.upsert_edge(
                    s,
                    src,
                    dst,
                    typ,
                    origin="ai",
                    valid_from=published,
                    exposure_kind=link.exposure_kind if fwd else None,
                    exposure_pct=link.exposure_pct if fwd else None,
                    confidence=link.confidence,
                    source=link.quote[:300],
                    evidence_news_id=news_id,
                )
    return added


def extract_live(engine: Engine | None = None, ai: AiClient | None = None) -> int:
    """Recent tagged items naming at least one F&O stock; one cheap call per item, never
    repeated."""
    c = cfg()
    now = datetime.now(IST)
    with session_scope(engine) as s:
        seen = list(get_config(s, SEEN, []) or [])
        today = str(now.date())
        used = int((get_config(s, "kg.extract.day", {}) or {}).get(today, 0))
        budget = min(int(c["max_items_per_run"]), int(c["max_items_per_day"]) - used)
        if budget <= 0:
            return 0
        ids = set(
            s.scalars(
                select(NewsTag.news_id).where(
                    NewsTag.symbol.is_not(None), NewsTag.prompt_ver.like("tagger%")
                )
            )
        )
        items = [
            (n.id, n.title, (n.body or "")[: int(c["body_chars"])], n.published_at or n.fetched_at)
            for n in s.scalars(
                select(NewsItem)
                .where(NewsItem.fetched_at >= now - timedelta(days=1))
                .order_by(NewsItem.id.desc())
            )
            if n.id in ids and n.id not in seen
        ][:budget]
    if not items:
        return 0
    ai = ai or AiClient(engine=engine)
    insts = _instruments(engine)
    added = 0
    for news_id, title, body, published in items:
        used += 1
        with session_scope(engine) as s:  # one AI call per item, whatever the result
            seen = [*seen, news_id]
            set_config(s, SEEN, seen[-5000:])
            set_config(s, "kg.extract.day", {today: used})
        try:
            out = ai.parse(
                "kg_extractor", SYSTEM, f"{title}\n\n{body}", Links, int(c["max_tokens"])
            )
        except (AiOutputError, BudgetExceededError, RuntimeError) as exc:
            log.info("kg link extract failed for %s: %s", news_id, exc)
            continue
        added += store_links(engine, out, published.date(), news_id, insts)
    return added


# ---- one-off history batch (owner approval) -------------------------------------------------
def history_items(engine: Engine | None = None) -> list[tuple[str, str, date]]:
    """(news_id, text, date) of historical items naming at least two F&O stocks."""
    from app.history.store import connect
    from app.processing.pipeline import build_mapper

    mapper = build_mapper(engine)
    out = []
    with connect() as con:
        for nid, ts, title, body in con.execute(
            "SELECT news_id, ts, title, body FROM hist_raw_news WHERE ts IS NOT NULL"
        ).fetchall():
            text = f"{title}\n\n{(body or '')[: int(cfg()['body_chars'])]}"
            if len(mapper.map(text).symbols) >= 2:
                out.append((str(nid), text, ts.date()))
    return out


def estimate(items: list[tuple[str, str, date]]) -> CostEstimate:
    c = cfg()
    return estimate_cost(
        "kg_extractor", len(items), int(c["est_input_tokens"]), int(c["est_output_tokens"])
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(prog="python -m app.kg.extract")
    p.add_argument("cmd", choices=["estimate", "submit", "collect"])
    p.add_argument("--yes", action="store_true", help="confirm the cost estimate was approved")
    a = p.parse_args(argv)
    if a.cmd in ("estimate", "submit"):
        items = history_items()
        est = estimate(items)
        print(f"Company-link history extraction: {est.summary()}")
        if a.cmd == "estimate" or not a.yes:
            if a.cmd == "submit":
                print("Not submitted. Re-run with --yes once the owner has approved this cost.")
            return 0
        ai = AiClient()
        ids = []
        for i in range(0, len(items), 5000):
            chunk = items[i : i + 5000]
            ids.append(
                ai.submit_batch(
                    "kg_extractor",
                    SYSTEM,
                    [(f"{n}|{d}", t) for n, t, d in chunk],
                    Links,
                    int(cfg()["max_tokens"]),
                )
            )
        with session_scope() as s:
            set_config(s, BATCHES, [*(get_config(s, BATCHES, []) or []), *ids])
        print(f"Submitted {len(ids)} batch(es).")
        return 0
    ai = AiClient()
    insts = _instruments(None)
    with session_scope() as s:
        pending = list(get_config(s, BATCHES, []) or [])
    left = []
    for bid in pending:
        if not ai.batch_done(bid):
            left.append(bid)
            continue
        for cid, out in ai.batch_results("kg_extractor", bid, Links).items():
            if out is not None:
                store_links(None, out, date.fromisoformat(cid.rsplit("|", 1)[1]), None, insts)
    with session_scope() as s:
        set_config(s, BATCHES, left)
    print(f"{len(pending) - len(left)} batch(es) collected, {len(left)} still running.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
