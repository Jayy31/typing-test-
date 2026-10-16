"""Relevance gate: decide whether an item is worth AI analysis."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import IST
from app.processing.entity_map import Mapping
from app.yaml_config import load_yaml


@dataclass(frozen=True)
class Decision:
    relevant: bool
    reason: str


def decide(
    title: str, mapping: Mapping, published_at: datetime | None, now: datetime | None = None
) -> Decision:
    cfg = load_yaml("relevance.yaml")
    now = now or datetime.now(IST)
    low = title.lower()
    if published_at and published_at.tzinfo is None:  # SQLite/test rows come back naive
        published_at = published_at.replace(tzinfo=IST)
    if len(title) < int(cfg["min_title_chars"]):
        return Decision(False, "title too short")
    if published_at and now - published_at > timedelta(hours=float(cfg["max_age_hours"])):
        return Decision(False, "too old")
    if not (mapping.symbols or mapping.sectors or mapping.themes):
        return Decision(False, "no F&O stock, sector or theme")
    drop = next((p for p in cfg["drop_title_patterns"] if p in low), None)
    if drop and not any(t in low for t in cfg["fact_triggers"]):
        return Decision(False, f"filler: '{drop}'")
    return Decision(True, "ok")
