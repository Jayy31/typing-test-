"""Processing job: entity mapping + relevance gate for freshly collected news."""

import logging

from sqlalchemy import Engine, select

from app.db.models import Instrument, NewsItem
from app.db.session import session_scope
from app.processing.entity_map import EntityMapper, InstrumentInfo
from app.processing.relevance import decide

log = logging.getLogger(__name__)


def build_mapper(engine: Engine | None = None) -> EntityMapper:
    with session_scope(engine) as s:
        insts = [
            InstrumentInfo(i.symbol, i.name, i.sector, tuple(i.aliases or []))
            for i in s.scalars(select(Instrument))
        ]
    return EntityMapper(insts)


def gate_news(engine: Engine | None = None, batch: int = 500) -> int:
    """Move 'pending' items to 'relevant' (goes to the AI tagger) or 'dropped'."""
    mapper = build_mapper(engine)
    with session_scope(engine) as s:
        items = list(
            s.scalars(
                select(NewsItem)
                .where(NewsItem.status == "pending")
                .order_by(NewsItem.id)
                .limit(batch)
            )
        )
        for item in items:
            mapping = mapper.map(f"{item.title}. {item.body or ''}")
            verdict = decide(item.title, mapping, item.published_at)
            item.status = "relevant" if verdict.relevant else "dropped"
        return len(items)
