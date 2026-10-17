"""Structured events -> candidate evidence.

A material structured event becomes a synthetic news row (source 'deep:<category>:<rule|base_rate>',
published_at = PUBLIC DISCLOSURE time) plus one tag with the tx2 subtype. Candidates, analyst
citations, /why and the live history sync then work unchanged, at no tagger cost.

Direction and strength: the hand-set rule in deep_data.yaml (low weight, kind structured_rule) until
the event study has at least ANALOGUE_MIN_N independent events for the subtype; then its base rate
replaces the rule (kind structured_base_rate, normal weight, counts as an official filing).
"""

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import taxonomy
from app.config import get_settings
from app.db.models import Instrument, NewsItem, NewsTag
from app.history import analogues
from app.yaml_config import load_yaml

PROMPT_VER = "rules:deep_data_v1"
Lookup = Callable[..., "analogues.AnalogueResult | None"]
KINDS = {"rule": "structured_rule", "base_rate": "structured_base_rate"}


@dataclass(frozen=True)
class Resolved:
    sentiment: float  # -1..1, sign = direction
    novelty: float  # strength factor
    source: str  # rule / base_rate
    detail: str  # shown in /why

    @property
    def direction(self) -> str:
        return "bullish" if self.sentiment > 0 else "bearish"


def rule_value(subtype: str) -> float:
    return float(load_yaml("deep_data.yaml")["evidence"]["rules"].get(subtype, 0.0))


def resolve(
    subtype: str,
    symbol: str | None,
    sector: str | None,
    when: datetime,
    lookup: Lookup | None = None,  # injectable for tests
) -> Resolved | None:
    cfg = load_yaml("deep_data.yaml")["evidence"]
    rule = rule_value(subtype)
    if rule == 0.0:
        return None
    rule_dir = "bullish" if rule > 0 else "bearish"
    res = (lookup or analogues.lookup)(
        [subtype], sector, symbol or "", rule_dir, when.date(), int(cfg["base_rate_window"])
    )
    if res is not None and res.usable:
        # mean is measured in the rule's direction: negative means the market moved the other way
        strength = math.tanh(res.mean / float(cfg["base_rate_scale_pct"]))
        sent = math.copysign(1.0, rule) * strength
        if abs(sent) < 1e-6:
            return None
        return Resolved(
            round(sent, 3),
            1.0,
            "base_rate",
            f"event-study base rate ({res.level}, n={res.n}, avg {res.mean:+.2f}% in the rule's "
            f"direction, hit {res.hit_rate:.0%}, {res.window}d)",
        )
    n = res.n if res is not None else 0
    return Resolved(
        rule,
        float(cfg["rule_novelty"]),
        "rule",
        f"hand-set rule (low weight; event study has n={n} < "
        f"{get_settings().analogue_min_n} events for {subtype})",
    )


def counts_as_official(source: str) -> bool:
    """Structured evidence that passes the candidate gate's 'official filing' test."""
    if source.startswith("kg:"):  # graph evidence with a learned sign counts like a base rate
        return source.split(":")[3] == "base_rate"
    if not source.startswith("deep:"):
        return False
    _, category, kind = source.split(":", 2)
    cats = load_yaml("deep_data.yaml")["evidence"].get("official_categories") or []
    return kind == "base_rate" or category in cats


def source_label(source: str) -> str | None:
    """/why text for a structured evidence source, None for normal news."""
    if not source.startswith("deep:"):
        return None
    kind = source.rsplit(":", 1)[-1]
    return "event-study base rate" if kind == "base_rate" else "hand-set rule (low weight)"


def emit(
    s: Session,
    *,
    category: str,
    ref: str,
    symbol: str,
    subtype: str,
    disclosed_at: datetime,
    title: str,
    url: str | None,
    detail: str,
    lookup: Lookup | None = None,
) -> int | None:
    """Create (once per ref + subtype) the evidence row. Returns its news id, None if no rule
    applies."""
    url_hash = hashlib.sha256(f"deep:{category}:{ref}:{subtype}".encode()).hexdigest()
    existing = s.scalar(select(NewsItem.id).where(NewsItem.url_hash == url_hash))
    if existing:
        return existing
    sector = s.scalar(select(Instrument.sector).where(Instrument.symbol == symbol))
    r = resolve(subtype, symbol, sector, disclosed_at, lookup)
    if r is None:
        return None
    item = NewsItem(
        url_hash=url_hash,
        source=f"deep:{category}:{r.source}",
        title=f"{symbol}: {title}"[:300],
        body=f"{detail}\nEvidence: {r.detail}",
        url=url or f"deep://{category}/{ref}",
        published_at=disclosed_at,
        status="tagged",
    )
    s.add(item)
    s.flush()
    s.add(
        NewsTag(
            news_id=item.id,
            symbol=symbol,
            sector=sector,
            event_type=subtype,
            direction=r.direction,
            sentiment=r.sentiment,
            novelty=r.novelty,
            model=None,
            prompt_ver=f"{PROMPT_VER}:{r.source}",
            taxonomy_ver=taxonomy.version(),
        )
    )
    return item.id
