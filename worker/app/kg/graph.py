"""Graph store on kg_nodes / kg_edges: building (seed file, sub-sector competitors, promoter
groups from insider filings, AI-extracted links), effective weights and point-in-time lookup."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import InsiderTrade, Instrument, KgEdge, KgNode
from app.db.session import session_scope
from app.yaml_config import load_yaml

SEED = Path(__file__).resolve().parents[3] / "data" / "kg_seed.yaml"
# Each extracted relation is stored in both directions (source = the stock with the event).
REVERSE = {
    "supplier_of": "customer_of",
    "customer_of": "supplier_of",
    "competitor_of": "competitor_of",
    "same_group": "same_group",
}


def cfg() -> dict[str, Any]:
    return load_yaml("kg.yaml")


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    type: str
    weight: float  # effective (base x exposure), before hop decay
    exposure_kind: str | None
    exposure_pct: float | None
    origin: str | None


def effective_weight(
    edge_type: str, exposure_kind: str | None, exposure_pct: float | None
) -> float:
    """base x min(1, exposure / ref); missing exposure -> base x missing_factor."""
    c = cfg()
    base = float(c["edge_types"][edge_type]["base_weight"])
    ref = (c["exposure"]["ref_pct"] or {}).get(exposure_kind or "")
    if exposure_pct is None or not ref:
        return round(base * float(c["exposure"]["missing_factor"]), 4)
    return round(base * min(1.0, max(exposure_pct, 0.0) / float(ref)), 4)


def node(s: Session, symbol: str) -> KgNode:
    n = s.scalars(select(KgNode).where(KgNode.kind == "company", KgNode.name == symbol)).first()
    if n is None:
        n = KgNode(kind="company", name=symbol, symbol=symbol)
        s.add(n)
        s.flush()
    return n


def upsert_edge(
    s: Session,
    src: str,
    dst: str,
    edge_type: str,
    *,
    origin: str,
    exposure_kind: str | None = None,
    exposure_pct: float | None = None,
    valid_from: date | None = None,
    confidence: float | None = None,
    source: str | None = None,
    evidence_news_id: int | None = None,
) -> bool:
    """Insert or refresh one directed edge. Returns True if it is new. Known exposure is never
    overwritten by a missing one; the earliest valid_from is kept."""
    if src == dst or edge_type not in cfg()["edge_types"]:
        return False
    a, b = node(s, src), node(s, dst)
    e = s.scalars(
        select(KgEdge).where(KgEdge.src == a.id, KgEdge.dst == b.id, KgEdge.type == edge_type)
    ).first()
    new = e is None
    if e is None:
        e = KgEdge(src=a.id, dst=b.id, type=edge_type, weight=0.0, origin=origin)
        s.add(e)
    if exposure_pct is not None:
        e.exposure_kind, e.exposure_pct = exposure_kind, exposure_pct
    if new or (valid_from and e.valid_from and valid_from < e.valid_from):
        e.valid_from = valid_from
    e.confidence = confidence if confidence is not None else e.confidence
    e.source = source or e.source
    e.evidence_news_id = evidence_news_id or e.evidence_news_id
    e.weight = effective_weight(edge_type, e.exposure_kind, e.exposure_pct)
    s.flush()
    return new


def build_static(engine: Engine | None = None, seed: Path = SEED) -> int:
    """Seed file + competitors within each sub-sector + same promoter group (insider filings).
    Structural edges have no valid_from (always valid). Idempotent."""
    added = 0
    with session_scope(engine) as s:
        insts = [i for i in s.scalars(select(Instrument)) if i.is_fno]
        known = {i.symbol for i in insts}
        by_sub: dict[str, list[str]] = {}
        for i in insts:
            if i.sub_sector:
                by_sub.setdefault(i.sub_sector, []).append(i.symbol)
        for members in by_sub.values():
            for a in members:
                for b in members:
                    added += upsert_edge(s, a, b, "competitor_of", origin="sub_sector")
        data = yaml.safe_load(seed.read_text(encoding="utf-8")) if seed.exists() else {}
        for e in (data or {}).get("edges") or []:
            if e["src"] in known and e["dst"] in known:
                exp = e.get("exposure") or {}
                added += upsert_edge(
                    s,
                    e["src"],
                    e["dst"],
                    e["type"],
                    origin="seed",
                    exposure_kind=exp.get("kind"),
                    exposure_pct=exp.get("pct"),
                )
        groups: dict[str, set[str]] = {}
        for sym, person in s.execute(
            select(InsiderTrade.symbol, InsiderTrade.person).where(
                InsiderTrade.counterparty.in_(("promoter", "promoter_group"))
            )
        ):
            if person and sym in known:
                groups.setdefault(person.strip().upper(), set()).add(sym)
        for syms in groups.values():  # one promoter entity filing for several stocks
            for a in syms:
                for b in syms:
                    added += upsert_edge(s, a, b, "same_group", origin="insider")
    return added


def neighbours(s: Session, symbol: str, as_of: date | None = None) -> list[Edge]:
    """Company edges out of `symbol` valid on `as_of` (structural edges always)."""
    src = s.scalars(select(KgNode).where(KgNode.kind == "company", KgNode.name == symbol)).first()
    if src is None:
        return []
    out: list[Edge] = []
    for e, dst in s.execute(
        select(KgEdge, KgNode).join(KgNode, KgNode.id == KgEdge.dst).where(KgEdge.src == src.id)
    ):
        if dst.kind != "company" or not dst.symbol:
            continue
        if as_of and (
            (e.valid_from and e.valid_from > as_of) or (e.valid_to and e.valid_to < as_of)
        ):
            continue
        out.append(
            Edge(symbol, dst.symbol, e.type, e.weight, e.exposure_kind, e.exposure_pct, e.origin)
        )
    return out
