"""Candidates: per-stock evidence from tagged news, with time-decayed weighted sentiment."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import flags, taxonomy
from app.db.models import NewsItem, NewsTag
from app.deep.evidence import counts_as_official
from app.timeutil import as_ist
from app.yaml_config import load_yaml


@dataclass(frozen=True)
class EvidenceItem:
    news_id: int
    tag_id: int
    source: str
    kind: str
    title: str
    summary: str | None
    published_at: datetime
    sentiment: float
    novelty: float
    event_type: str | None
    themes: tuple[str, ...]
    weight: float
    cluster_id: int | None = None


@dataclass
class Candidate:
    symbol: str
    items: list[EvidenceItem] = field(default_factory=list)
    agg_sentiment: float = 0.0
    strength: float = 0.0

    @property
    def direction(self) -> str:
        return "bullish" if self.agg_sentiment > 0 else "bearish"

    @property
    def sources(self) -> set[str]:
        return {i.source for i in self.items}

    @property
    def has_official(self) -> bool:
        # structured evidence counts when backed by the event study or measured from the filing
        return any(i.kind == "official" or counts_as_official(i.source) for i in self.items)

    @property
    def impact_order(self) -> int:
        """1 if any direct evidence, else the fewest graph hops (2nd / 3rd order)."""
        if any(not i.source.startswith("kg:") for i in self.items):
            return 1
        return 1 + min(int(i.source.split(":")[1]) for i in self.items)

    @property
    def max_tag_id(self) -> int:
        return max(i.tag_id for i in self.items)

    @property
    def themes(self) -> frozenset[str]:
        return frozenset(t for i in self.items for t in i.themes)

    @property
    def event_types(self) -> frozenset[str]:
        """Event types incl. the parent of each structured subtype (playbooks key on parents)."""
        out: set[str] = set()
        for i in self.items:
            if i.event_type:
                out.add(i.event_type)
                out.add(taxonomy.parent(i.event_type) or i.event_type)
        return frozenset(out)


def source_kind(source: str) -> str:
    if source.startswith("kg:"):  # kg:<hops>:<edge>:<default|base_rate>:<role> (Phase 8 graph)
        return "kg_base_rate" if source.split(":")[3] == "base_rate" else "kg_rule"
    if source.startswith("deep:"):  # deep:<category>:<rule|base_rate> (Phase 7 structured events)
        return "structured_base_rate" if source.endswith(":base_rate") else "structured_rule"
    if source == "nse_announcements":
        return "official"
    for feed in load_yaml("news_sources.yaml")["feeds"]:
        if feed["name"] == source:
            return str(feed["kind"])
    return "news"


def decay(age_hours: float, half_life_hours: float) -> float:
    return float(0.5 ** (max(age_hours, 0.0) / half_life_hours))


def weighted_sentiment(items: list[tuple[float, float]]) -> float:
    """items = [(weight, sentiment)] -> weighted mean, 0 when there is no weight."""
    total = sum(w for w, _ in items)
    return sum(w * s for w, s in items) / total if total else 0.0


def evidence_for(
    s: Session, now: datetime, symbol: str | None = None
) -> list[tuple[str | None, str | None, EvidenceItem]]:
    """All tagged evidence in the window as (symbol, sector, item)."""
    cfg = load_yaml("signals.yaml")["candidates"]
    weights = load_yaml("news_sources.yaml")["kind_weights"]
    window = timedelta(hours=float(cfg["evidence_window_hours"]))
    q = select(NewsTag, NewsItem).join(NewsItem, NewsItem.id == NewsTag.news_id)
    if symbol:
        q = q.where(NewsTag.symbol == symbol)
    out: list[tuple[str | None, str | None, EvidenceItem]] = []
    deep_on = flags.active(s, "feat.deep_data")  # owner switch for Phase 7 structured evidence
    for tag, item in s.execute(q).all():
        if not deep_on and item.source.startswith("deep:"):
            continue
        when = as_ist(item.published_at or item.fetched_at)
        age_h = (now - when).total_seconds() / 3600
        if age_h > window.total_seconds() / 3600 or tag.sentiment is None:
            continue
        kind = source_kind(item.source)
        out.append(
            (
                tag.symbol,
                tag.sector,
                EvidenceItem(
                    news_id=item.id,
                    tag_id=tag.id,
                    source=item.source,
                    kind=kind,
                    title=item.title,
                    summary=item.body,
                    published_at=when,
                    sentiment=tag.sentiment,
                    novelty=tag.novelty or 0.0,
                    event_type=tag.event_type,
                    themes=tuple(t for t in (tag.theme or "").split(",") if t),
                    weight=float(weights.get(kind, 0.5))
                    * decay(age_h, float(cfg["half_life_hours"])),
                    cluster_id=item.cluster_id,
                ),
            )
        )
    return out


def build_candidates(
    s: Session, now: datetime, rejected: list[tuple[Candidate, str]] | None = None
) -> list[Candidate]:
    """Stocks whose evidence is strong enough to deserve a deep analysis, strongest first.
    Weak ones are appended to `rejected` with the reason (if a list is given)."""
    cfg = load_yaml("signals.yaml")["candidates"]
    by_symbol: dict[str, Candidate] = {}
    for symbol, _sector, item in evidence_for(s, now):
        if symbol:
            by_symbol.setdefault(symbol, Candidate(symbol)).items.append(item)
    out: list[Candidate] = []
    for cand in by_symbol.values():
        cand.agg_sentiment = weighted_sentiment([(i.weight, i.sentiment) for i in cand.items])
        cand.strength = abs(cand.agg_sentiment) * max(i.novelty for i in cand.items)
        if cand.strength < float(cfg["min_strength"]):
            if rejected is not None:
                rejected.append(
                    (cand, f"evidence strength {cand.strength:.2f} below {cfg['min_strength']}")
                )
            continue
        if not (
            cand.has_official
            or len(cand.sources) >= 2
            or cand.strength >= float(cfg["strong_single_source"])
        ):
            if rejected is not None:
                rejected.append((cand, "single unofficial source without strong evidence"))
            continue
        cand.items.sort(key=lambda i: abs(i.sentiment) * i.weight, reverse=True)
        out.append(cand)
    return sorted(out, key=lambda c: c.strength, reverse=True)
