"""2nd/3rd-order evidence: direct evidence on a stock propagates along graph edges.

For each recent direct evidence item (news, filings, structured events) on stock A and each path
A -> B (-> C, up to kg.yaml max_depth, default 2):
  sentiment = source sentiment x product of edge signs x product of sign strengths
  strength factor (novelty) = product of edge weights (exposure-scaled) x hop_decay^(hops - 1)
- Sign 'unknown' on any hop: support-only path, recorded for /why, no evidence row.
- No double counting: if B already has evidence from the same news event (same item or cluster,
  directly or via a sector-level tag for B's sector), the path is added as SUPPORTING evidence
  (role 'support'), not as a new graph candidate.
- At most max_propagated_per_day new graph-only targets per day; the rest are recorded ('kg_cap').
Evidence rows are synthetic news rows (source 'kg:<hops>:<edge>:<basis>:<role>'), so candidates,
the analyst, /why and the history sync see them like any other evidence.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import taxonomy
from app.db.models import Instrument, KgPath, NewsItem, NewsTag
from app.kg import graph, signs
from app.signals.candidates import EvidenceItem, evidence_for
from app.signals.rejections import record_rejection
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PROMPT_VER = "rules:kg_v1"


@dataclass(frozen=True)
class Hop:
    edge: graph.Edge
    sign: signs.EdgeSign


def is_graph(source: str) -> bool:
    return source.startswith("kg:")


def hops_of(source: str) -> int:
    """kg:<hops>:<edge>:<basis>:<role> -> hops; 1 for direct evidence."""
    return int(source.split(":")[1]) if is_graph(source) else 1


def basis_of(source: str) -> str | None:
    return source.split(":")[3] if is_graph(source) else None


def role_of(source: str) -> str | None:
    return source.split(":")[4] if is_graph(source) else None


def _covered(evidence: list[tuple[str | None, str | None, EvidenceItem]]) -> dict[str, set[str]]:
    """News event key (item / cluster) -> stocks (or 'sector:<name>') it already gives direct
    evidence for."""
    out: dict[str, set[str]] = {}
    for sym, sector, it in evidence:
        if is_graph(it.source):
            continue
        keys = [f"n{it.news_id}"] + ([f"c{it.cluster_id}"] if it.cluster_id else [])
        for k in keys:
            if sym:
                out.setdefault(k, set()).add(sym)
            elif sector:
                out.setdefault(k, set()).add(f"sector:{sector}")
    return out


def propagate(s: Session, now: datetime) -> dict[str, int]:
    c = load_yaml("kg.yaml")["propagation"]
    max_depth, decay = int(c["max_depth"]), float(c["hop_decay"])
    window = timedelta(hours=float(c["source_window_hours"]))
    evidence = evidence_for(s, now)
    covered = _covered(evidence)
    sectors: dict[str, str | None] = {i.symbol: i.sector for i in s.scalars(select(Instrument))}
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    prop_today = set(
        s.scalars(
            select(KgPath.target).where(KgPath.role == "propagated", KgPath.created_at >= day_start)
        )
    )
    counts = {"propagated": 0, "support": 0, "unknown": 0, "capped": 0}
    for sym, _sector, it in evidence:
        if not sym or is_graph(it.source) or now - it.published_at > window:
            continue
        keys = [f"n{it.news_id}"] + ([f"c{it.cluster_id}"] if it.cluster_id else [])
        already = set().union(*(covered.get(k, set()) for k in keys))
        frontier: list[tuple[str, list[Hop]]] = [(sym, [])]
        for depth in range(1, max_depth + 1):
            nxt: list[tuple[str, list[Hop]]] = []
            for node, path in frontier:
                for e in graph.neighbours(s, node, now.date()):
                    seen = {sym, *(h.edge.dst for h in path)}
                    if e.dst in seen:
                        continue
                    sg = signs.resolve(e.type, it.event_type, e.dst, sectors.get(e.dst), now.date())
                    new_path = [*path, Hop(e, sg)]
                    nxt.append((e.dst, new_path))
                    role = _record(
                        s,
                        it,
                        sym,
                        e.dst,
                        new_path,
                        depth,
                        decay,
                        already,
                        sectors,
                        prop_today,
                        int(c["max_propagated_per_day"]),
                        float(c["min_abs_sentiment"]),
                        now,
                    )
                    if role:
                        counts[role] += 1
            frontier = nxt
    return counts


def _record(
    s: Session,
    it: EvidenceItem,
    src: str,
    target: str,
    path: list[Hop],
    hops: int,
    decay: float,
    already: set[str],
    sectors: dict[str, str | None],
    prop_today: set[str],
    cap: int,
    min_abs: float,
    now: datetime,
) -> str | None:
    key = ">".join([src] + [f"{h.edge.type}>{h.edge.dst}" for h in path])[:256]
    if s.scalar(
        select(KgPath.id).where(
            KgPath.source_news_id == it.news_id, KgPath.target == target, KgPath.path_key == key
        )
    ):
        return None
    sign = 1
    strength = 1.0
    weight = decay ** (hops - 1)
    for h in path:
        sign *= h.sign.sign
        strength *= h.sign.strength
        weight *= h.edge.weight
    basis = (
        "unknown"
        if sign == 0
        else "base_rate"
        if all(h.sign.basis == "base_rate" for h in path)
        else "default"
    )
    sentiment = round(it.sentiment * sign * strength, 3)
    detail = {
        "source_title": it.title,
        "hops": [
            {
                "src": h.edge.src,
                "type": h.edge.type,
                "dst": h.edge.dst,
                "weight": h.edge.weight,
                "exposure": [h.edge.exposure_kind, h.edge.exposure_pct],
                "sign": h.sign.sign,
                "basis": h.sign.basis,
                "why": h.sign.detail,
            }
            for h in path
        ],
    }
    covered = target in already or f"sector:{sectors.get(target)}" in already
    if basis == "unknown":
        role = "unknown"
    elif covered:
        role = "support"
    elif target not in prop_today and len(prop_today) >= cap:
        role = "capped"
    else:
        role = "propagated"
    row = KgPath(
        source_news_id=it.news_id,
        source_symbol=src,
        target=target,
        path_key=key,
        hops=hops,
        role=role,
        sign_basis=basis,
        weight=round(weight, 4),
        sentiment=sentiment,
        detail_json=detail,
        created_at=now,
    )
    s.add(row)
    if role == "capped":
        record_rejection(
            s,
            dedupe_key=f"kg_cap:{it.news_id}:{target}",
            ts=now,
            symbol=target,
            stage="kg_cap",
            reason=f"graph propagation cap ({cap}/day) reached",
            details_json={"path": key},
        )
        return role
    if role == "unknown" or abs(sentiment) < min_abs or weight <= 0:
        return role
    if role == "propagated":
        prop_today.add(target)
    row.news_id = _evidence_row(
        s, it, target, key, hops, path, basis, role, sentiment, weight, sectors
    )
    return role


def _evidence_row(
    s: Session,
    it: EvidenceItem,
    target: str,
    key: str,
    hops: int,
    path: list[Hop],
    basis: str,
    role: str,
    sentiment: float,
    weight: float,
    sectors: dict[str, str | None],
) -> int:
    url_hash = hashlib.sha256(f"kg:{it.news_id}:{target}:{key}".encode()).hexdigest()
    existing = s.scalar(select(NewsItem.id).where(NewsItem.url_hash == url_hash))
    if existing:
        return int(existing)
    last = path[-1].edge.type
    item = NewsItem(
        url_hash=url_hash,
        source=f"kg:{hops}:{last}:{basis}:{role}",
        title=f"{target}: via {key.replace('>', ' > ')} | {it.title}"[:300],
        body="\n".join(
            f"{h.edge.src} {h.edge.type} {h.edge.dst} (weight {h.edge.weight:.2f}): {h.sign.detail}"
            for h in path
        ),
        url=f"kg://{it.news_id}/{target}",
        published_at=it.published_at,  # the source's (disclosure) time
        status="tagged",
    )
    s.add(item)
    s.flush()
    s.add(
        NewsTag(
            news_id=item.id,
            symbol=target,
            sector=sectors.get(target),
            event_type=signs.kg_event_type(last, it.event_type),
            direction="bullish" if sentiment > 0 else "bearish",
            sentiment=sentiment,
            novelty=round(min(1.0, weight), 4),
            model=None,
            prompt_ver=f"{PROMPT_VER}:{basis}",
            taxonomy_ver=taxonomy.version(),
        )
    )
    return int(item.id)


def kg_label(source: str) -> str:
    """/why line for a graph evidence row."""
    basis = basis_of(source)
    what = {
        "base_rate": "sign from the event-study base rate",
        "default": "sign from the default table (low weight)",
    }.get(basis or "", "sign unknown")
    role = (
        "supporting evidence (stock already covered by the same news)"
        if role_of(source) == "support"
        else "graph-propagated"
    )
    return f"Graph path, {hops_of(source)} hop(s), {role}; {what}:"


def propagated_today(s: Session, now: datetime) -> int:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        s.scalar(
            select(func.count(func.distinct(KgPath.target))).where(
                KgPath.role == "propagated", KgPath.created_at >= start
            )
        )
        or 0
    )
